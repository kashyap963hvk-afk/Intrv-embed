"""Local Model Bridge API: pair a PC with a 6-digit code, list and revoke devices, and the device's tunnel."""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.audit.service import ClientMeta, record
from app.auth.deps import ActiveMember, ValkeyDep, csrf_protect
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.ratelimit import enforce_rate_limit
from app.core.resources import Resources
from app.db.deps import DbSession
from app.db.models import BridgeDevice, User
from app.db.tenant import TenantContext, set_tenant_context
from app.llm.bridge_hub import BridgeRequestError, Hello

log = get_logger("app.llm.bridge")

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
ws_router = APIRouter()

PAIR_TTL_S = 600
PAIR_DIGITS = 6
TOKEN_PREFIX = "ibr_"  # noqa: S105 - a public prefix that makes leaked tokens easy to recognise


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pair_key(code: str) -> str:
    return f"bridge:pair:{code}"


class PairOut(BaseModel):
    code: str
    expires_in_s: int


class ClaimIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=120)
    platform: str = Field(default="", max_length=60)


class ClaimOut(BaseModel):
    device_id: uuid.UUID
    token: str = Field(description="Shown once; the bridge stores it. Only a hash is kept on the server.")


class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    platform: str
    online: bool
    models: list[dict[str, Any]]
    capabilities: dict[str, Any]
    last_seen_at: datetime | None
    created_at: datetime


def _resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


@router.post("/pair", response_model=PairOut, dependencies=[Depends(csrf_protect)])
async def pair(principal: ActiveMember, valkey: ValkeyDep) -> PairOut:
    """A one-time code the member types into the bridge app on their PC (valid 10 minutes)."""
    await enforce_rate_limit(valkey, f"bridge-pair:{principal.user_id}", limit=10, window_s=3600)
    code = f"{secrets.randbelow(10**PAIR_DIGITS):0{PAIR_DIGITS}d}"
    owner = json.dumps({"org_id": str(principal.org_id), "user_id": str(principal.user_id)})
    await valkey.set(_pair_key(code), owner, ex=PAIR_TTL_S)
    return PairOut(code=code, expires_in_s=PAIR_TTL_S)


@router.post("/claim", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
async def claim(body: ClaimIn, request: Request, db: DbSession, valkey: ValkeyDep) -> ClaimOut:
    """Called by the bridge app (no browser session): exchanges a pairing code for a device token."""
    ip = request.client.host if request.client else "unknown"
    await enforce_rate_limit(valkey, f"bridge-claim:ip:{ip}", limit=20, window_s=3600)
    raw = await valkey.getdel(_pair_key(body.code))  # single use
    if raw is None:
        raise NotFoundError("That pairing code is wrong or has expired. Create a new one in AI models.")
    owner = json.loads(raw)
    org_id, user_id = uuid.UUID(owner["org_id"]), uuid.UUID(owner["user_id"])
    await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id))
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("That pairing code is wrong or has expired. Create a new one in AI models.")
    await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=user.role.value))
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    device = BridgeDevice(
        id=uuid.uuid4(),
        org_id=org_id,
        owner_user_id=user_id,
        name=body.name.strip(),
        platform=body.platform.strip(),
        token_hash=hash_token(token),
    )
    db.add(device)
    await record(
        db,
        org_id=org_id,
        actor_id=user_id,
        action="bridge.paired",
        meta=ClientMeta.from_request(request),
        target_type="bridge_device",
        target_id=device.id,
        detail={"name": device.name, "platform": device.platform},
    )
    await db.flush()
    return ClaimOut(device_id=device.id, token=token)


@router.get("/devices", response_model=list[DeviceOut])
async def devices(_member: ActiveMember, db: DbSession, request: Request) -> list[DeviceOut]:
    hub = _resources(request).bridge
    rows = await db.scalars(
        select(BridgeDevice).where(BridgeDevice.revoked_at.is_(None)).order_by(BridgeDevice.created_at)
    )  # RLS: only the member's own devices
    return [
        DeviceOut(
            id=d.id,
            name=d.name,
            platform=d.platform,
            online=await hub.online(d.id),
            models=d.models,
            capabilities=d.capabilities,
            last_seen_at=d.last_seen_at,
            created_at=d.created_at,
        )
        for d in rows
    ]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(csrf_protect)])
async def revoke(device_id: uuid.UUID, principal: ActiveMember, db: DbSession, request: Request) -> None:
    device = await db.get(BridgeDevice, device_id)
    if device is None or device.revoked_at is not None:
        raise NotFoundError("Device not found.")
    device.revoked_at = datetime.now(UTC)
    await record(
        db,
        org_id=principal.org_id,
        actor_id=principal.user_id,
        action="bridge.revoked",
        meta=ClientMeta.from_request(request),
        target_type="bridge_device",
        target_id=device.id,
        detail={"name": device.name},
    )
    await _resources(request).bridge.disconnect(device.id)


@ws_router.websocket("/api/bridge/connect")
async def connect(websocket: WebSocket) -> None:
    """The device's tunnel. Authenticated with `Authorization: Bearer <device token>`."""
    resources: Resources = websocket.app.state.resources
    header = websocket.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    async with resources.session_factory() as db:
        row = (
            await db.execute(
                sql_text("SELECT id, org_id, owner_user_id FROM app_bridge_device_by_token(:h)"),
                {"h": hash_token(token)},
            )
        ).first()
    if not token or row is None:
        await websocket.close(code=4401, reason="Unknown or revoked device; pair the bridge again.")
        return
    device_id, org_id, user_id = row[0], row[1], row[2]
    await websocket.accept()

    async def on_hello(hello: Hello) -> None:
        async with resources.session_factory() as db:
            await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id))
            device = await db.get(BridgeDevice, device_id)
            if device is not None:
                device.models, device.capabilities = hello.models, {**hello.capabilities, "version": hello.version}
                device.last_seen_at = datetime.now(UTC)
                await db.commit()
        await websocket.send_json({"type": "welcome", "device_id": str(device_id)})

    try:
        await resources.bridge.serve(device_id, websocket, on_hello)
    except (WebSocketDisconnect, TimeoutError) as exc:
        log.info("bridge.disconnected", device_id=str(device_id), reason=type(exc).__name__)
    except BridgeRequestError as exc:
        log.info("bridge.closed", device_id=str(device_id), reason=exc.kind)
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=4403 if exc.kind == "revoked" else 4400, reason=exc.message[:120])

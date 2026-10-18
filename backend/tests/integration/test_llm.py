"""LLM gateway end to end: API, encryption at rest, probing, routing, fallbacks, policy, privacy."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import redis

from app.core.config import Settings
from app.core.keyring import Keyring
from app.core.ssrf import create_http_client
from app.db.models import AgentRole, Role, UsageEvent
from app.db.session import create_engine, create_session_factory
from app.db.tenant import TenantContext, set_tenant_context
from app.llm.factory import make_provider_factory
from app.llm.gateway import GatewayConfig, LLMGateway
from app.llm.limits import ProfileLimiter
from app.llm.service import LLMService, PolicyViolationError
from app.llm.types import CallCtx
from tests.fakes.openai_server import GOOD_KEY, running_server
from tests.integration.conftest import Postgres
from tests.integration.helpers import ApiClient, app_settings, new_client, seed_org, seed_user

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def fake_llm() -> Iterator[str]:
    with running_server() as (base_url, _app):
        yield base_url


@pytest.fixture
def settings(postgres: str, fresh_valkey: str) -> Settings:
    # Self-host mode so the test can reach the fake provider on 127.0.0.1.
    return app_settings(postgres, fresh_valkey, allow_private_llm_endpoints=True)


@pytest.fixture
def owner(settings: Settings) -> Iterator[tuple[ApiClient, Any]]:
    org = seed_org(settings)
    api = new_client(settings)
    assert api.login(org.owner_email).status_code == 200
    yield api, org
    api.close()


def create_profile(api: ApiClient, base_url: str, model: str = "good", **extra: Any) -> dict[str, Any]:
    body = {"name": f"{model}-{uuid.uuid4().hex[:4]}", "base_url": base_url, "api_key": GOOD_KEY, "model": model}
    response = api.post("/api/llm/profiles", json={**body, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_api_key_is_encrypted_and_never_returned(
    pg: Postgres, owner: tuple[ApiClient, Any], fake_llm: str, capsys: pytest.CaptureFixture[str]
) -> None:
    api, _ = owner
    profile = create_profile(api, fake_llm)
    assert profile["has_api_key"] is True
    assert profile["api_key_last4"] is None  # 'sk-good' is too short to reveal any of it
    for response in (api.get("/api/llm/profiles"), api.get(f"/api/llm/profiles/{profile['id']}/models")):
        assert GOOD_KEY not in response.text
    with psycopg.connect(pg.dsn("postgres")) as conn:
        stored = conn.execute("SELECT encrypted_api_key FROM llm_profiles WHERE id = %s", (profile["id"],)).fetchone()
    assert stored is not None
    assert GOOD_KEY.encode() not in bytes(stored[0])
    assert GOOD_KEY not in capsys.readouterr().out  # never logged


def test_probe_rates_the_model(owner: tuple[ApiClient, Any], fake_llm: str) -> None:
    api, _ = owner
    profile = create_profile(api, fake_llm)
    probed = api.post(f"/api/llm/profiles/{profile['id']}/probe")
    assert probed.status_code == 200, probed.text
    body = probed.json()
    assert body["status"] == "OK"
    caps = body["capabilities"]
    assert all(caps[name] for name in ("stream", "json_schema", "tools", "vision"))
    assert caps["quality_score"] == 5
    assert body["quality_tier"] == "EXCELLENT"

    plain = create_profile(api, fake_llm, model="plain")
    plain_caps = api.post(f"/api/llm/profiles/{plain['id']}/probe").json()["capabilities"]
    assert plain_caps["json_schema"] is False
    assert plain_caps["vision"] is False


def test_probe_reports_bad_key(owner: tuple[ApiClient, Any], fake_llm: str) -> None:
    api, _ = owner
    profile = create_profile(api, fake_llm, api_key="sk-wrong-key-123456")
    body = api.post(f"/api/llm/profiles/{profile['id']}/probe").json()
    assert body["status"] == "ERROR"
    assert "rejected" in body["last_error"]
    assert body["api_key_last4"] == "3456"


def test_discover_models_and_try(owner: tuple[ApiClient, Any], fake_llm: str) -> None:
    api, _ = owner
    found = api.post("/api/llm/models/discover", json={"base_url": fake_llm, "api_key": GOOD_KEY})
    assert found.status_code == 200, found.text
    assert "good" in found.json()["models"]
    profile = create_profile(api, fake_llm)
    answer = api.post(f"/api/llm/profiles/{profile['id']}/try", json={"message": "hello there"}).json()
    assert answer["text"] == "echo: hello there"
    assert answer["tokens_out"] >= 1


def _service_call(
    settings: Settings, org_id: uuid.UUID, user_id: uuid.UUID, **ctx_kwargs: Any
) -> tuple[str, list[UsageEvent]]:
    async def run() -> tuple[str, list[UsageEvent]]:
        engine = create_engine(settings)
        valkey = redis.asyncio.Redis.from_url(settings.valkey_url)
        public, private = create_http_client(allow_private=False), create_http_client(allow_private=True)
        try:
            factory = make_provider_factory(settings, public, private)
            gateway = LLMGateway(
                factory, ProfileLimiter(valkey), GatewayConfig(max_retries=1, backoff_base_s=0, backoff_cap_s=0)
            )
            async with create_session_factory(engine)() as db:
                await set_tenant_context(db, TenantContext(org_id=org_id, user_id=user_id, role=Role.OWNER.value))
                service = LLMService(db, settings=settings, keyring=Keyring(settings.master_key), gateway=gateway)
                ctx = CallCtx(org_id=org_id, user_id=user_id, role=AgentRole.INTERVIEWER, **ctx_kwargs)
                completion = await service.complete(ctx, [{"role": "user", "content": "ping"}])
                usage = [e for e in db.new if isinstance(e, UsageEvent)]
                await db.commit()
                return completion.text, usage
        finally:
            await public.aclose()
            await private.aclose()
            await valkey.aclose()
            await engine.dispose()

    return asyncio.run(run())


def test_routing_falls_back_and_records_usage(settings: Settings, owner: tuple[ApiClient, Any], fake_llm: str) -> None:
    api, org = owner
    failing = create_profile(api, fake_llm, model="fail-429", scope="org", trains_on_data=False)
    good = create_profile(api, fake_llm, model="good", scope="org", trains_on_data=False)
    routes = api.put("/api/llm/routes", json={"scope": "org", "routes": {"interviewer": [failing["id"], good["id"]]}})
    assert routes.status_code == 200, routes.text

    text, usage = _service_call(settings, org.id, org.owner_id)
    assert text == "echo: ping"
    assert [(str(u.profile_id), u.ok) for u in usage] == [
        (failing["id"], False),
        (failing["id"], False),
        (good["id"], True),
    ]


def test_circuit_breaker_opens_after_repeated_failures(
    settings: Settings, owner: tuple[ApiClient, Any], fake_llm: str
) -> None:
    api, org = owner
    failing = create_profile(api, fake_llm, model="fail-500", scope="org", trains_on_data=False)
    good = create_profile(api, fake_llm, model="good", scope="org", trains_on_data=False)
    api.put("/api/llm/routes", json={"scope": "org", "routes": {"interviewer": [failing["id"], good["id"]]}})
    for _ in range(3):
        _service_call(settings, org.id, org.owner_id)
    breaker_open = redis.Redis.from_url(settings.valkey_url).exists(f"llm:cb:open:{failing['id']}")
    assert breaker_open
    _, usage = _service_call(settings, org.id, org.owner_id)
    assert [str(u.profile_id) for u in usage] == [good["id"]]  # the broken profile is skipped while open


@pytest.mark.isolation
def test_candidate_data_never_goes_to_training_providers(
    settings: Settings, owner: tuple[ApiClient, Any], fake_llm: str
) -> None:
    api, org = owner
    risky = create_profile(api, fake_llm, model="good", scope="org")  # no preset: data policy unknown
    api.put("/api/llm/routes", json={"scope": "org", "routes": {"interviewer": [risky["id"]]}})
    assert _service_call(settings, org.id, org.owner_id)[0] == "echo: ping"  # fine without PII
    with pytest.raises(PolicyViolationError, match="training"):
        _service_call(settings, org.id, org.owner_id, contains_pii=True)

    # Owners can relax the org policy explicitly (audited).
    policy = api.get("/api/admin/org-policy").json()
    assert policy["block_training_providers_for_pii"] is True
    api.put("/api/admin/org-policy", json={**policy, "block_training_providers_for_pii": False})
    assert _service_call(settings, org.id, org.owner_id, contains_pii=True)[0] == "echo: ping"
    assert api.get("/api/admin/audit", params={"action": "org.policy_changed"}).json()["items"]


@pytest.mark.isolation
def test_personal_profiles_are_private(settings: Settings, owner: tuple[ApiClient, Any], fake_llm: str) -> None:
    owner_api, org = owner
    shared = create_profile(owner_api, fake_llm, scope="org")
    _, alice_email = seed_user(settings, org)
    _, bob_email = seed_user(settings, org)
    alice, bob = new_client(settings), new_client(settings)
    try:
        alice.login(alice_email)
        bob.login(bob_email)
        private = create_profile(alice, fake_llm)
        bob_view = {p["id"] for p in bob.get("/api/llm/profiles").json()}
        assert shared["id"] in bob_view
        assert private["id"] not in bob_view
        assert private["id"] not in {p["id"] for p in owner_api.get("/api/llm/profiles").json()}
        assert bob.post(f"/api/llm/profiles/{private['id']}/probe").status_code == 404
        assert bob.delete(f"/api/llm/profiles/{private['id']}").status_code == 404
        mine = bob.put("/api/llm/routes", json={"scope": "mine", "routes": {"grader": [private["id"]]}})
        assert mine.status_code == 422
        # Members cannot create or change org-wide models or routes.
        assert (
            bob.post(
                "/api/llm/profiles", json={"name": "x", "base_url": fake_llm, "model": "good", "scope": "org"}
            ).status_code
            == 403
        )
        assert bob.delete(f"/api/llm/profiles/{shared['id']}").status_code == 403
        assert (
            bob.put("/api/llm/routes", json={"scope": "org", "routes": {"grader": [shared["id"]]}}).status_code == 403
        )
        assert (
            bob.post(
                "/api/llm/profiles", json={"name": "x", "base_url": fake_llm, "model": "good", "trains_on_data": False}
            ).status_code
            == 403
        )
    finally:
        alice.close()
        bob.close()


def test_member_without_permission_cannot_add_models(
    settings: Settings, owner: tuple[ApiClient, Any], fake_llm: str
) -> None:
    owner_api, org = owner
    user_id, email = seed_user(settings, org)
    owner_api.patch(f"/api/admin/users/{user_id}/policy", json={"can_create_llm_profiles": False})
    member = new_client(settings)
    try:
        member.login(email)
        response = member.post("/api/llm/profiles", json={"name": "x", "base_url": fake_llm, "model": "good"})
        assert response.status_code == 403
    finally:
        member.close()


def test_ssrf_destinations_rejected_in_cloud_mode(postgres: str, fresh_valkey: str) -> None:
    cloud = app_settings(postgres, fresh_valkey)  # allow_private_llm_endpoints=False
    org = seed_org(cloud)
    api = new_client(cloud)
    try:
        api.login(org.owner_email)
        for url in (
            "http://169.254.169.254/v1",
            "https://127.0.0.1/v1",
            "https://localhost:8000/v1",
            "http://api.example.com/v1",
        ):
            response = api.post("/api/llm/profiles", json={"name": "x", "base_url": url, "model": "m", "api_key": "k"})
            assert response.status_code == 422, (url, response.text)
        local = api.post("/api/llm/profiles", json={"name": "x", "preset_key": "ollama", "model": "m"})
        assert local.status_code == 422
        assert "Local Model Bridge" in local.json()["detail"]
    finally:
        api.close()

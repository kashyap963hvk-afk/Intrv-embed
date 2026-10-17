"""Process-wide clients created in the FastAPI lifespan and shared by request handlers."""

from __future__ import annotations

from dataclasses import dataclass

import boto3
import httpx
import httpx2
from botocore.config import Config as BotoConfig
from mypy_boto3_s3 import S3Client
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.keyring import Keyring
from app.core.ssrf import create_http_client
from app.db.session import create_engine, create_session_factory
from app.llm.factory import make_provider_factory
from app.llm.gateway import LLMGateway, ProviderFactory
from app.llm.limits import ProfileLimiter


def create_s3_client(settings: Settings) -> S3Client:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else None,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None,
        config=BotoConfig(
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


@dataclass(slots=True)
class Resources:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    valkey: Redis
    http: httpx.AsyncClient
    s3: S3Client
    keyring: Keyring
    # Outbound clients for user-supplied destinations: SSRF-guarded, and an unguarded one for self-host mode.
    public_http: httpx2.AsyncClient
    private_http: httpx2.AsyncClient
    provider_factory: ProviderFactory
    gateway: LLMGateway

    @classmethod
    def create(cls, settings: Settings) -> Resources:
        engine = create_engine(settings)
        valkey = Redis.from_url(settings.valkey_url, socket_connect_timeout=3, socket_timeout=5)
        public_http = create_http_client(allow_private=False)
        private_http = create_http_client(allow_private=True)
        provider_factory = make_provider_factory(settings, public_http, private_http)
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            valkey=valkey,
            http=httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)),
            s3=create_s3_client(settings),
            keyring=Keyring(settings.master_key),
            public_http=public_http,
            private_http=private_http,
            provider_factory=provider_factory,
            gateway=LLMGateway(provider_factory, ProfileLimiter(valkey)),
        )

    async def close(self) -> None:
        await self.public_http.aclose()
        await self.private_http.aclose()
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()
        self.s3.close()

"""Process-wide clients created in the FastAPI lifespan and shared by request handlers."""

from __future__ import annotations

from dataclasses import dataclass

import boto3
import httpx
from botocore.config import Config as BotoConfig
from mypy_boto3_s3 import S3Client
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import create_engine, create_session_factory


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

    @classmethod
    def create(cls, settings: Settings) -> Resources:
        engine = create_engine(settings)
        return cls(
            engine=engine,
            session_factory=create_session_factory(engine),
            valkey=Redis.from_url(settings.valkey_url, socket_connect_timeout=3, socket_timeout=5),
            http=httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)),
            s3=create_s3_client(settings),
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.valkey.aclose()
        await self.engine.dispose()
        self.s3.close()

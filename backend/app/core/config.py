"""Application settings, loaded from environment variables (and `.env` in development).

Every variable from docs/ARCHITECTURE.md §20 is declared here so that misconfiguration fails at
startup, not at the first request that happens to need it.
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["dev", "test", "prod"]

MASTER_KEY_BYTES = 32
MIN_JWT_SECRET_LENGTH = 32
_PLACEHOLDER_SECRETS = {"change-me", "changeme", "secret", "dev", "test", "password"}


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _split_pathsep(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(os.pathsep) if item.strip()]
    return value


CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # `SMTP_USER=` in .env means "not set", not "the empty string".
        env_ignore_empty=True,
    )

    # --- Application -------------------------------------------------------------------------
    app_env: AppEnv = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    public_app_url: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    cors_allowed_origins: CsvList = Field(default_factory=lambda: ["http://localhost:3000"])
    cookie_domain: str | None = None
    metrics_enabled: bool = True
    readiness_timeout_s: float = Field(default=2.0, gt=0, le=30)

    # --- Data stores -------------------------------------------------------------------------
    database_url: str
    database_migration_url: str | None = None
    db_pool_size: int = Field(default=10, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    valkey_url: str
    qdrant_url: str
    qdrant_api_key: SecretStr | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    s3_bucket: str
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_force_path_style: bool = True
    # Endpoint browsers use for presigned download links, when it differs from the one the backend uses
    # (e.g. backend → http://seaweedfs:8333 inside Docker, browser → http://localhost:8333).
    s3_public_endpoint_url: str | None = None

    # --- Secrets -----------------------------------------------------------------------------
    master_key_base64: SecretStr
    jwt_secret: SecretStr
    owner_bootstrap_email: str | None = None
    owner_bootstrap_org_name: str = "My Organization"
    owner_bootstrap_org_slug: str = "default"

    # --- Auth ----------------------------------------------------------------------------------
    access_token_ttl_s: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    invite_ttl_hours: int = Field(default=168, ge=1, le=720)
    allow_org_signup: bool = False
    cookie_secure: bool | None = None  # default: secure when API_PUBLIC_URL is https

    # --- Retrieval models --------------------------------------------------------------------
    # quality: tei + BAAI/bge-m3 (+ TEI reranker bge-reranker-v2-m3); lite: fastembed + bge-small-en-v1.5.
    # "hash" is a deterministic lexical embedder for tests and offline development only (refused in production).
    embed_backend: Literal["tei", "fastembed", "openai", "hash"] = "fastembed"
    embed_model: str = "BAAI/bge-small-en-v1.5"
    tei_url: str | None = None
    embed_openai_base_url: str | None = None
    embed_openai_api_key: SecretStr | None = None
    embed_batch_size: int = Field(default=32, ge=1, le=512)
    rerank_backend: Literal["tei", "fastembed", "none"] = "none"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    tei_rerank_url: str | None = None
    models_cache_dir: str = "/tmp/intrv-models"  # noqa: S108  # nosec B108 - overridden by a volume in containers

    # --- Ingestion -----------------------------------------------------------------------------
    parser_backend: Literal["auto", "basic", "docling"] = "auto"
    max_upload_mb: int = Field(default=100, ge=1, le=2048)
    max_pages: int = Field(default=2000, ge=1)
    ingest_timeout_s: int = Field(default=900, ge=30)
    # inline: process uploads inside the API process (tests, single-process demos); otherwise Celery workers.
    ingest_inline: bool = False

    # --- Role packs ----------------------------------------------------------------------------
    # Directory of shipped (SYSTEM) role packs; each org gets read-only copies it can fork.
    role_packs_dir: str = str(Path(__file__).resolve().parents[2] / "seed" / "role_packs")

    # Speech-to-text and text-to-speech run in the voice worker (voice/), not in the API (ADR 0009).

    # --- Agent engine / LLM ------------------------------------------------------------------
    engine_turn_mode: Literal["auto", "single_call", "two_call"] = "auto"
    allow_private_llm_endpoints: bool = False
    gguf_allowed_dirs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    llama_server_bin: str = "llama-server"  # GGUF_MANAGED: llama.cpp server binary (path or name on PATH)
    gguf_idle_unload_s: int = Field(default=900, ge=60)

    # --- Connectors --------------------------------------------------------------------------
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    google_picker_api_key: str | None = None
    google_service_account_json_base64: SecretStr | None = None
    ms_client_id: str | None = None
    ms_client_secret: SecretStr | None = None
    ms_tenant: str = "common"
    dropbox_app_key: str | None = None
    dropbox_app_secret: SecretStr | None = None

    # --- Realtime media ----------------------------------------------------------------------
    turn_urls: CsvList = Field(default_factory=list)
    turn_username: str | None = None
    turn_credential: SecretStr | None = None
    turn_shared_secret: SecretStr | None = None
    stun_urls: CsvList = Field(default_factory=lambda: ["stun:stun.l.google.com:19302"])
    voice_public_url: str | None = (
        None  # the voice service (docker compose profile "voice"), e.g. https://voice.example.com
    )
    turn_credential_ttl_s: int = Field(default=3600, ge=300, le=86_400)

    # --- Email -------------------------------------------------------------------------------
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_tls: Literal["none", "starttls", "ssl"] = "starttls"
    mail_from: str | None = None

    # --- Practical rounds --------------------------------------------------------------------
    piston_url: str | None = None
    renode_enabled: bool = False

    # --- Observability -----------------------------------------------------------------------
    otel_exporter_otlp_endpoint: str | None = None
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None

    _split_csv_fields = field_validator("cors_allowed_origins", "turn_urls", "stun_urls", mode="before")(_split_csv)
    _split_dirs = field_validator("gguf_allowed_dirs", mode="before")(_split_pathsep)

    @field_validator("database_url", "database_migration_url")
    @classmethod
    def _require_asyncpg(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("postgresql+asyncpg://"):
            raise ValueError("must use the postgresql+asyncpg:// scheme")
        return value

    @field_validator("valkey_url")
    @classmethod
    def _require_redis_scheme(cls, value: str) -> str:
        if urlparse(value).scheme not in {"redis", "rediss"}:
            raise ValueError("must use the redis:// or rediss:// scheme")
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _no_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("'*' is not allowed: credentials (cookies) require explicit origins")
        for origin in value:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
                raise ValueError(f"invalid origin {origin!r}: expected scheme://host[:port]")
        return [origin.rstrip("/") for origin in value]

    @field_validator("master_key_base64")
    @classmethod
    def _master_key_is_32_bytes(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("must be valid base64") from exc
        if len(raw) != MASTER_KEY_BYTES:
            raise ValueError("must decode to exactly 32 bytes (generate with: openssl rand -base64 32)")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_strength(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < MIN_JWT_SECRET_LENGTH or secret.lower() in _PLACEHOLDER_SECRETS:
            raise ValueError("must be at least 32 characters of random data")
        return value

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Settings:
        if self.embed_backend == "hash" and self.app_env == "prod":
            raise ValueError("EMBED_BACKEND=hash is for tests and offline development only")
        if self.embed_backend == "tei" and not self.tei_url:
            raise ValueError("EMBED_BACKEND=tei requires TEI_URL")
        if self.rerank_backend == "tei" and not self.tei_rerank_url:
            raise ValueError("RERANK_BACKEND=tei requires TEI_RERANK_URL")
        if self.embed_backend == "openai" and not (self.embed_openai_base_url and self.embed_model):
            raise ValueError("EMBED_BACKEND=openai requires EMBED_OPENAI_BASE_URL")
        if self.gguf_allowed_dirs and not self.allow_private_llm_endpoints:
            raise ValueError("gguf_allowed_dirs requires allow_private_llm_endpoints=true (self-host mode)")
        if self.app_env != "prod":
            return self
        insecure = [
            name
            for name, url in (("public_app_url", self.public_app_url), ("api_public_url", self.api_public_url))
            if urlparse(url).scheme != "https"
        ]
        insecure += [f"cors_allowed_origins[{o}]" for o in self.cors_allowed_origins if not o.startswith("https://")]
        if insecure:
            raise ValueError(f"production requires https for: {', '.join(insecure)}")
        return self

    @property
    def master_key(self) -> bytes:
        return base64.b64decode(self.master_key_base64.get_secret_value())

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return urlparse(self.api_public_url).scheme == "https"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment

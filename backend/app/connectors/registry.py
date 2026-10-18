"""Builds the right connector for an account (or a URL source) from its decrypted credentials."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2

from app.connectors.azure_blob import AzureBlobConnector
from app.connectors.base import ConnectorError, StorageConnector
from app.connectors.dropbox import DropboxConnector
from app.connectors.google_drive import GoogleDriveConnector, ServiceAccountTokens
from app.connectors.ms_graph import GraphConnector
from app.connectors.oauth import OAuthTokens, TokenSaver, TokenSet, oauth_providers
from app.connectors.s3 import S3Connector
from app.connectors.url import UrlConnector
from app.core.config import Settings
from app.db.models import ConnectorKind

OAUTH_KINDS: Mapping[ConnectorKind, str] = {
    ConnectorKind.GOOGLE_DRIVE: "google",
    ConnectorKind.MS_GRAPH: "microsoft",
    ConnectorKind.DROPBOX: "dropbox",
}
CREDENTIAL_KINDS = frozenset({ConnectorKind.S3_COMPATIBLE, ConnectorKind.AZURE_BLOB})


@dataclass(frozen=True, slots=True)
class ConnectorClients:
    settings: Settings
    provider_http: httpx.AsyncClient  # fixed provider APIs (Google, Microsoft, Dropbox)
    web_http: httpx2.AsyncClient  # SSRF-guarded client for user-supplied URLs


def availability(settings: Settings) -> dict[ConnectorKind, str | None]:
    """None when a kind can be used, otherwise the reason it cannot (shown in the "+" dialog)."""
    providers = oauth_providers(settings)
    return {
        ConnectorKind.GOOGLE_DRIVE: None if "google" in providers else "Set GOOGLE_OAUTH_CLIENT_ID/SECRET.",
        ConnectorKind.GOOGLE_DRIVE_ORG: None
        if settings.google_service_account_json_base64
        else "Set GOOGLE_SERVICE_ACCOUNT_JSON_BASE64.",
        ConnectorKind.MS_GRAPH: None if "microsoft" in providers else "Set MS_CLIENT_ID/MS_CLIENT_SECRET.",
        ConnectorKind.DROPBOX: None if "dropbox" in providers else "Set DROPBOX_APP_KEY/DROPBOX_APP_SECRET.",
        ConnectorKind.S3_COMPATIBLE: None,
        ConnectorKind.AZURE_BLOB: None,
        ConnectorKind.URL: None,
    }


def build_connector(
    clients: ConnectorClients,
    kind: ConnectorKind,
    config: Mapping[str, Any],
    secret: Mapping[str, Any],
    save_tokens: TokenSaver | None = None,
) -> StorageConnector:
    settings = clients.settings
    allow_private = settings.allow_private_llm_endpoints
    if kind in CREDENTIAL_KINDS:
        bucket_type = S3Connector if kind == ConnectorKind.S3_COMPATIBLE else AzureBlobConnector
        return bucket_type(config, secret, allow_private=allow_private)
    if kind == ConnectorKind.URL:
        return UrlConnector(clients.web_http)
    if kind == ConnectorKind.GOOGLE_DRIVE_ORG:
        if not settings.google_service_account_json_base64:
            raise ConnectorError("Org-wide Google Drive needs GOOGLE_SERVICE_ACCOUNT_JSON_BASE64.")
        tokens = ServiceAccountTokens(settings.google_service_account_json_base64.get_secret_value())
        return GoogleDriveConnector(kind, clients.provider_http, tokens, config)
    provider = oauth_providers(settings).get(OAUTH_KINDS[kind])
    if provider is None:
        raise ConnectorError(f"{kind.value} is not configured on this server.")
    oauth = OAuthTokens(clients.provider_http, provider, TokenSet.from_secret(dict(secret)), save_tokens)
    if kind == ConnectorKind.GOOGLE_DRIVE:
        return GoogleDriveConnector(kind, clients.provider_http, oauth, config)
    return (GraphConnector if kind == ConnectorKind.MS_GRAPH else DropboxConnector)(clients.provider_http, oauth)

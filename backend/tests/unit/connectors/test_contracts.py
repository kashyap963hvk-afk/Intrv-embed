"""Contract tests for the OAuth connectors against recorded provider responses (respx), plus scope rules."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.connectors.base import ConnectorError, ScopeError, Selection, confine, diff_snapshot, importable, within
from app.connectors.dropbox import API as DROPBOX_API
from app.connectors.dropbox import CONTENT as DROPBOX_CONTENT
from app.connectors.dropbox import DropboxConnector
from app.connectors.google_drive import API as DRIVE_API
from app.connectors.google_drive import GoogleDriveConnector
from app.connectors.ms_graph import API as GRAPH_API
from app.connectors.ms_graph import GraphConnector
from app.connectors.oauth import OAuthProvider, OAuthTokens, TokenSet
from app.db.models import ConnectorKind

MB = 1024 * 1024


class StaticTokens:
    def __init__(self) -> None:
        self.refreshes = 0

    async def access_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            self.refreshes += 1
        return f"token-{self.refreshes}"


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


# --- scope helpers -----------------------------------------------------------------------------------------


def test_confine_never_escapes_the_root() -> None:
    assert confine("team-a", None) == "team-a/"
    assert confine("team-a", "cvs") == "team-a/cvs/"
    assert confine("", "cvs/2026") == "cvs/2026/"
    assert confine("team-a", "/etc") == "team-a/etc/"  # a leading slash is still relative to the root
    for bad in ("../team-b", "cvs/../../team-b", "a\\b", "x\x00"):
        with pytest.raises(ScopeError):
            confine("team-a", bad)
    assert within("team-a", "team-a/cv.pdf")
    assert not within("team-a", "team-ab/cv.pdf")
    assert within("", "anything")


def test_under_root_accepts_full_keys_and_relative_paths() -> None:
    from app.connectors.base import under_root  # noqa: PLC0415

    assert under_root("run/team-a", "run/team-a/sub/") == "run/team-a/sub/"  # id as returned by browse
    assert under_root("run/team-a", "sub") == "run/team-a/sub/"
    assert under_root("run/team-a", None) == "run/team-a/"
    assert under_root("team-a", "team-ab/x") == "team-a/team-ab/x/"  # a look-alike prefix stays inside the root
    with pytest.raises(ScopeError):
        under_root("run/team-a", "run/team-a/../../team-b")


def test_importable_filters_by_type() -> None:
    assert importable("cv.pdf")
    assert importable("Notes", "application/vnd.google-apps.document")
    assert not importable("demo.mp4")
    assert not importable("archive.zip")


def test_diff_snapshot() -> None:
    from app.connectors.base import RemoteItem  # noqa: PLC0415

    listed = [
        RemoteItem("a", "a.md", "a.md", False, etag="1"),
        RemoteItem("b", "b.md", "b.md", False, etag="2"),
        RemoteItem("dir", "dir", "dir", True),
    ]
    changes = diff_snapshot(listed, {"a": "1", "b": "old", "gone": "x"})
    assert [i.id for i in changes.upserts] == ["b"]
    assert changes.deleted == ["gone"]


# --- Google Drive --------------------------------------------------------------------------------------------


@respx.mock
async def test_drive_lists_folder_recursively_and_exports_google_docs(http: httpx.AsyncClient) -> None:
    respx.get(f"{DRIVE_API}/files/folder1").respond(
        json={"id": "folder1", "name": "Hiring", "mimeType": "application/vnd.google-apps.folder"}
    )

    def children(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        if "'folder1' in parents" in query:
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": "cv1",
                            "name": "cv.pdf",
                            "mimeType": "application/pdf",
                            "md5Checksum": "m1",
                            "size": "10",
                        },
                        {
                            "id": "doc1",
                            "name": "JD",
                            "mimeType": "application/vnd.google-apps.document",
                            "version": "7",
                        },
                        {"id": "vid", "name": "intro.mp4", "mimeType": "video/mp4", "md5Checksum": "v"},
                        {"id": "sub", "name": "2026", "mimeType": "application/vnd.google-apps.folder"},
                    ]
                },
            )
        assert "'sub' in parents" in query
        return httpx.Response(
            200, json={"files": [{"id": "n1", "name": "notes.md", "mimeType": "text/markdown", "md5Checksum": "m3"}]}
        )

    respx.get(f"{DRIVE_API}/files").mock(side_effect=children)
    export = respx.get(f"{DRIVE_API}/files/doc1/export").respond(content=b"DOCX")
    respx.get(f"{DRIVE_API}/files/cv1").respond(content=b"%PDF-1.4")

    drive = GoogleDriveConnector(ConnectorKind.GOOGLE_DRIVE, http, StaticTokens(), {})
    selection = Selection(items=({"id": "folder1", "is_folder": True},))
    changes = await drive.changes(selection, {}, {"cv1": "m1", "removed": "x"})
    names = sorted(i.name for i in changes.upserts)
    assert names == ["JD.docx", "notes.md"]  # cv unchanged, video skipped, Google Doc exported as .docx
    assert changes.deleted == ["removed"]
    jd = next(i for i in changes.upserts if i.id == "doc1")
    assert jd.etag == "v7"
    assert jd.path == "Hiring/JD.docx"
    fetched = await drive.fetch(jd, MB)
    assert fetched.data == b"DOCX"
    assert export.calls.last.request.url.params["mimeType"].endswith("wordprocessingml.document")


@respx.mock
async def test_drive_org_mode_refuses_folders_outside_the_shared_roots(http: httpx.AsyncClient) -> None:
    respx.get(f"{DRIVE_API}/files/inside").respond(json={"id": "inside", "parents": ["root1"]})
    respx.get(f"{DRIVE_API}/files/outside").respond(json={"id": "outside", "parents": ["someone-elses"]})
    respx.get(f"{DRIVE_API}/files/someone-elses").respond(json={"id": "someone-elses", "parents": []})
    respx.get(f"{DRIVE_API}/files").respond(json={"files": []})
    drive = GoogleDriveConnector(ConnectorKind.GOOGLE_DRIVE_ORG, http, StaticTokens(), {"root_folder_ids": ["root1"]})
    assert (await drive.browse("inside", None)).items == []
    with pytest.raises(ScopeError):
        await drive.browse("outside", None)
    with pytest.raises(ScopeError):
        await drive.changes(Selection(items=({"id": "outside", "is_folder": True},)), {}, {})


@respx.mock
async def test_expired_token_is_refreshed_once_then_reported(http: httpx.AsyncClient) -> None:
    route = respx.get(f"{DRIVE_API}/about").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"user": {"emailAddress": "a@example.com"}})]
    )
    tokens = StaticTokens()
    drive = GoogleDriveConnector(ConnectorKind.GOOGLE_DRIVE, http, tokens, {})
    assert (await drive.validate()).detail == "Connected as a@example.com"
    assert tokens.refreshes == 1
    assert route.calls[1].request.headers["Authorization"] == "Bearer token-1"
    route.mock(side_effect=[httpx.Response(401), httpx.Response(401)])
    with pytest.raises(ConnectorError) as raised:
        await drive.validate()
    assert raised.value.auth


@respx.mock
async def test_downloads_are_size_capped(http: httpx.AsyncClient) -> None:
    respx.get(f"{DRIVE_API}/files/big").respond(content=b"x" * (MB + 1))
    drive = GoogleDriveConnector(ConnectorKind.GOOGLE_DRIVE, http, StaticTokens(), {})
    from app.connectors.base import RemoteItem  # noqa: PLC0415

    with pytest.raises(ConnectorError, match="larger than"):
        await drive.fetch(RemoteItem("big", "big.pdf", "big.pdf", False, mime="application/pdf"), MB)


# --- Microsoft Graph -----------------------------------------------------------------------------------------


@respx.mock
async def test_graph_full_listing_then_delta(http: httpx.AsyncClient) -> None:
    respx.get(f"{GRAPH_API}/drives/d1/items/f1/children").respond(
        json={
            "value": [
                {
                    "id": "a",
                    "name": "spec.pdf",
                    "file": {"mimeType": "application/pdf"},
                    "cTag": "c1",
                    "parentReference": {"driveId": "d1", "path": "/drive/root:/Hiring"},
                },
                {
                    "id": "s",
                    "name": "old",
                    "folder": {},
                    "parentReference": {"driveId": "d1", "path": "/drive/root:/Hiring"},
                },
            ]
        }
    )
    respx.get(f"{GRAPH_API}/drives/d1/items/s/children").respond(
        json={
            "value": [
                {
                    "id": "b",
                    "name": "notes.md",
                    "file": {},
                    "cTag": "c2",
                    "parentReference": {"driveId": "d1", "path": "/drive/root:/Hiring/old"},
                }
            ]
        }
    )
    respx.get(f"{GRAPH_API}/drives/d1/root/delta", params={"token": "latest"}).respond(
        json={"@odata.deltaLink": f"{GRAPH_API}/drives/d1/root/delta?token=T1"}
    )
    graph = GraphConnector(http, StaticTokens())
    selection = Selection(items=({"id": "d1/f1", "is_folder": True, "path": "/Hiring"},))
    first = await graph.changes(selection, {}, {})
    assert sorted(i.id for i in first.upserts) == ["d1/a", "d1/b"]
    assert first.cursor == {"delta": {"d1": f"{GRAPH_API}/drives/d1/root/delta?token=T1"}}

    respx.get(f"{GRAPH_API}/drives/d1/root/delta", params={"token": "T1"}).respond(
        json={
            "value": [
                {
                    "id": "a",
                    "name": "spec.pdf",
                    "file": {},
                    "cTag": "c1-new",
                    "parentReference": {"driveId": "d1", "path": "/drive/root:/Hiring"},
                },
                {"id": "b", "deleted": {"state": "deleted"}, "name": "notes.md"},
                {
                    "id": "x",
                    "name": "payroll.xlsx",
                    "file": {},
                    "cTag": "z",
                    "parentReference": {"driveId": "d1", "path": "/drive/root:/Finance"},
                },
            ],
            "@odata.deltaLink": f"{GRAPH_API}/drives/d1/root/delta?token=T2",
        }
    )
    second = await graph.changes(selection, first.cursor, {"d1/a": "c1", "d1/b": "c2"})
    assert [i.id for i in second.upserts] == ["d1/a"]  # Finance is outside the selection
    assert second.deleted == ["d1/b"]
    assert second.cursor["delta"]["d1"].endswith("token=T2")


@respx.mock
async def test_graph_expired_delta_link_falls_back_to_full_listing(http: httpx.AsyncClient) -> None:
    respx.get(f"{GRAPH_API}/drives/d1/root/delta", params={"token": "OLD"}).respond(status_code=410)
    respx.get(f"{GRAPH_API}/drives/d1/items/f1/children").respond(json={"value": []})
    respx.get(f"{GRAPH_API}/drives/d1/root/delta", params={"token": "latest"}).respond(
        json={"@odata.deltaLink": f"{GRAPH_API}/drives/d1/root/delta?token=NEW"}
    )
    graph = GraphConnector(http, StaticTokens())
    selection = Selection(items=({"id": "d1/f1", "is_folder": True, "path": "/Hiring"},))
    result = await graph.changes(
        selection, {"delta": {"d1": f"{GRAPH_API}/drives/d1/root/delta?token=OLD"}}, {"d1/a": "c"}
    )
    assert result.deleted == ["d1/a"]
    assert result.cursor["delta"]["d1"].endswith("token=NEW")


@respx.mock
async def test_graph_refuses_pagination_links_to_other_hosts(http: httpx.AsyncClient) -> None:
    graph = GraphConnector(http, StaticTokens())
    with pytest.raises(ConnectorError, match="unexpected host"):
        await graph.browse(None, "https://attacker.example/steal")


# --- Dropbox -------------------------------------------------------------------------------------------------


@respx.mock
async def test_dropbox_cursor_flow(http: httpx.AsyncClient) -> None:
    respx.post(f"{DROPBOX_API}/files/list_folder").respond(
        json={
            "entries": [
                {
                    ".tag": "file",
                    "name": "cv.pdf",
                    "path_lower": "/hiring/cv.pdf",
                    "path_display": "/Hiring/cv.pdf",
                    "content_hash": "h1",
                    "size": 5,
                },
                {".tag": "folder", "name": "old", "path_lower": "/hiring/old"},
                {".tag": "file", "name": "a.md", "path_lower": "/hiring/old/a.md", "content_hash": "h2"},
            ],
            "cursor": "C1",
            "has_more": False,
        }
    )
    dropbox = DropboxConnector(http, StaticTokens())
    selection = Selection(items=({"id": "/hiring", "is_folder": True},))
    first = await dropbox.changes(selection, {}, {})
    assert sorted(i.id for i in first.upserts) == ["/hiring/cv.pdf", "/hiring/old/a.md"]
    assert first.cursor == {"folders": {"/hiring": "C1"}}

    def continue_(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"cursor": "C1"}
        return httpx.Response(
            200,
            json={
                "entries": [
                    {".tag": "deleted", "name": "old", "path_lower": "/hiring/old"},
                    {".tag": "file", "name": "cv.pdf", "path_lower": "/hiring/cv.pdf", "content_hash": "h1b"},
                ],
                "cursor": "C2",
                "has_more": False,
            },
        )

    respx.post(f"{DROPBOX_API}/files/list_folder/continue").mock(side_effect=continue_)
    second = await dropbox.changes(selection, first.cursor, {"/hiring/cv.pdf": "h1", "/hiring/old/a.md": "h2"})
    assert [i.id for i in second.upserts] == ["/hiring/cv.pdf"]
    assert second.deleted == ["/hiring/old/a.md"]  # deleting a folder removes what was inside
    assert second.cursor == {"folders": {"/hiring": "C2"}}

    download = respx.post(f"{DROPBOX_CONTENT}/files/download").respond(content=b"%PDF")
    fetched = await dropbox.fetch(second.upserts[0], MB)
    assert fetched.data == b"%PDF"
    assert json.loads(download.calls.last.request.headers["Dropbox-API-Arg"]) == {"path": "/hiring/cv.pdf"}


@respx.mock
async def test_dropbox_reset_cursor_relists(http: httpx.AsyncClient) -> None:
    respx.post(f"{DROPBOX_API}/files/list_folder/continue").respond(status_code=409, json={"error": {".tag": "reset"}})
    respx.post(f"{DROPBOX_API}/files/list_folder").respond(json={"entries": [], "cursor": "fresh", "has_more": False})
    dropbox = DropboxConnector(http, StaticTokens())
    selection = Selection(items=({"id": "/hiring", "is_folder": True},))
    result = await dropbox.changes(selection, {"folders": {"/hiring": "stale"}}, {"/hiring/x.md": "h"})
    assert result.deleted == ["/hiring/x.md"]
    assert result.cursor == {"folders": {"/hiring": "fresh"}}


# --- OAuth token refresh -----------------------------------------------------------------------------------


@respx.mock
async def test_oauth_tokens_refresh_and_persist(http: httpx.AsyncClient) -> None:
    provider = OAuthProvider(
        key="dropbox",
        kind=ConnectorKind.DROPBOX,
        label="Dropbox",
        authorize_url="https://www.dropbox.com/oauth2/authorize",
        token_url="https://api.dropboxapi.com/oauth2/token",
        scopes=(),
        client_id="cid",
        client_secret="csecret",
    )
    token_route = respx.post(provider.token_url).respond(json={"access_token": "fresh", "expires_in": 3600})
    saved: list[TokenSet] = []

    async def save(tokens: TokenSet) -> None:
        saved.append(tokens)

    tokens = OAuthTokens(http, provider, TokenSet("stale", "refresh-1", time.time() - 10), save)
    assert await tokens.access_token() == "fresh"
    assert await tokens.access_token() == "fresh"  # cached until near expiry
    assert token_route.call_count == 1
    form = dict(httpx.QueryParams(token_route.calls.last.request.content.decode()))
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "refresh-1"
    assert saved[0].refresh_token == "refresh-1"  # providers may omit it on refresh; the old one is kept

    no_refresh = OAuthTokens(http, provider, TokenSet("stale", None, 0), save)
    with pytest.raises(ConnectorError) as raised:
        await no_refresh.access_token()
    assert raised.value.auth

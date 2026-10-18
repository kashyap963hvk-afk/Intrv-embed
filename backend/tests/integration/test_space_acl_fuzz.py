"""Randomised check of space permissions: the SQL functions and RLS must agree with an independent oracle.

Each round writes a random arrangement of spaces, groups, memberships and (possibly expired) grants, then for
every user compares `app_space_permissions()` and the documents visible through RLS against a Python model of
the rules in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field

import psycopg
import pytest

from tests.integration.conftest import Postgres

pytestmark = [pytest.mark.integration, pytest.mark.isolation]

ROUNDS = 40  # x 5 users = 200 user/arrangement combinations


@dataclass
class Arrangement:
    org: uuid.UUID
    users: dict[uuid.UUID, str] = field(default_factory=dict)  # id -> role
    groups: dict[uuid.UUID, set[uuid.UUID]] = field(default_factory=dict)  # id -> members
    spaces: dict[uuid.UUID, tuple[str, uuid.UUID | None]] = field(default_factory=dict)  # id -> (kind, owner)
    grants: list[tuple[uuid.UUID, str, uuid.UUID, str, bool]] = field(default_factory=list)


def _random_arrangement(rng: random.Random) -> Arrangement:
    arr = Arrangement(org=uuid.uuid4())
    roles = ["OWNER"] + [rng.choice(["MEMBER", "MEMBER", "OWNER"]) for _ in range(4)]
    for role in roles:
        arr.users[uuid.uuid4()] = role
    user_ids = list(arr.users)
    for _ in range(rng.randint(0, 3)):
        arr.groups[uuid.uuid4()] = set(rng.sample(user_ids, rng.randint(0, len(user_ids))))
    for _ in range(rng.randint(1, 4)):
        arr.spaces[uuid.uuid4()] = ("ORG", None)
    for _ in range(rng.randint(0, 3)):
        arr.spaces[uuid.uuid4()] = ("PERSONAL", rng.choice(user_ids))
    for space_id, (kind, _) in arr.spaces.items():
        if kind != "ORG":
            continue
        principals = [("USER", u) for u in user_ids] + [("GROUP", g) for g in arr.groups]
        for principal_type, principal_id in rng.sample(principals, rng.randint(0, len(principals))):
            permission = rng.choice(["READ", "CONTRIBUTE"])
            expired = rng.random() < 0.25
            arr.grants.append((space_id, principal_type, principal_id, permission, expired))
    return arr


def _oracle(arr: Arrangement, user: uuid.UUID) -> dict[uuid.UUID, str]:
    result: dict[uuid.UUID, str] = {}

    def give(space: uuid.UUID, permission: str) -> None:
        if result.get(space) != "CONTRIBUTE":
            result[space] = permission

    for space_id, (kind, owner) in arr.spaces.items():
        if kind == "PERSONAL" and owner == user:
            give(space_id, "CONTRIBUTE")
        if kind == "ORG" and arr.users[user] == "OWNER":
            give(space_id, "CONTRIBUTE")
    for space_id, principal_type, principal_id, permission, expired in arr.grants:
        if expired:
            continue
        if (principal_type == "USER" and principal_id == user) or (
            principal_type == "GROUP" and user in arr.groups[principal_id]
        ):
            give(space_id, permission)
    return result


def _write(conn: psycopg.Connection, arr: Arrangement) -> None:
    conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (%s, 'fuzz', %s)", (arr.org, f"fuzz-{arr.org.hex[:12]}")
    )
    for user_id, role in arr.users.items():
        conn.execute(
            "INSERT INTO users (id, org_id, email, name, role, status) VALUES (%s, %s, %s, 'u', %s, 'ACTIVE')",
            (user_id, arr.org, f"{user_id.hex[:10]}@fuzz.example.com", role),
        )
    for group_id, members in arr.groups.items():
        conn.execute(
            "INSERT INTO groups (id, org_id, name, description) VALUES (%s, %s, %s, '')",
            (group_id, arr.org, group_id.hex[:10]),
        )
        for member in members:
            conn.execute(
                "INSERT INTO group_members (group_id, user_id, org_id) VALUES (%s, %s, %s)", (group_id, member, arr.org)
            )
    for space_id, (kind, owner) in arr.spaces.items():
        conn.execute(
            "INSERT INTO knowledge_spaces (id, org_id, kind, owner_user_id, name, description) "
            "VALUES (%s, %s, %s, %s, 's', '')",
            (space_id, arr.org, kind, owner),
        )
        conn.execute(
            "INSERT INTO documents (id, org_id, space_id, title, size_bytes, version, doc_type, status, chunk_count) "
            "VALUES (gen_random_uuid(), %s, %s, 'd', 0, 1, 'KNOWLEDGE', 'READY', 0)",
            (arr.org, space_id),
        )
    for space_id, principal_type, principal_id, permission, expired in arr.grants:
        conn.execute(
            "INSERT INTO space_grants (id, org_id, space_id, principal_type, principal_id, permission, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, CASE WHEN %s THEN now() - interval '1 hour' "
            "ELSE now() + interval '1 day' END)",
            (arr.org, space_id, principal_type, principal_id, permission, expired),
        )


def test_permissions_match_the_oracle(pg: Postgres) -> None:
    rng = random.Random(20261017)  # noqa: S311 - deterministic fuzzing, not cryptography
    checked = 0
    with psycopg.connect(pg.dsn("postgres"), autocommit=True) as admin, psycopg.connect(pg.dsn("app_rw")) as rw:
        for _ in range(ROUNDS):
            arr = _random_arrangement(rng)
            _write(admin, arr)
            for user_id, role in arr.users.items():
                rw.execute(
                    "SELECT set_config('app.org_id', %s, true), set_config('app.user_id', %s, true), "
                    "set_config('app.role', %s, true)",
                    (str(arr.org), str(user_id), role),
                )
                actual = dict(rw.execute("SELECT space_id, permission FROM app_space_permissions()").fetchall())
                expected = _oracle(arr, user_id)
                assert actual == expected, (arr, user_id)
                visible_docs = {
                    row[0] for row in rw.execute("SELECT space_id FROM documents WHERE org_id = %s", (arr.org,))
                }
                assert visible_docs == set(expected)
                visible_spaces = {row[0] for row in rw.execute("SELECT id FROM knowledge_spaces")}
                assert visible_spaces == set(expected)
                rw.rollback()
                checked += 1
    assert checked >= 200

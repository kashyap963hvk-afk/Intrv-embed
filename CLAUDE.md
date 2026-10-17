# CLAUDE.md: engineering rules for this repository

You are building **Intrv-embed**, an agentic, human-like AI interviewer (Embedded-Systems-first, any role via Role Packs).
The spec is `docs/ARCHITECTURE.md`. The build plan is `PROMPT.md`. Read both before changing code. If code and spec disagree, stop and fix the spec first (with an ADR in `docs/adr/`), or fix the code to match.

## Hard rules
1. **No placeholders.** No `TODO`, `pass`, `...`, `NotImplementedError` or mock data in production paths. If something is out of scope for the current phase, don't stub it: leave it out and list it in `docs/BACKLOG.md`. (Candidate starter code inside role packs is data, so it is exempt.)
2. **Never break tenant isolation.** Every DB query is scoped by `org_id` and ownership. Every vector search goes through `rag.retrieve()` with an ACL filter built server-side. Never accept `org_id`, `user_id`, `space_id` lists or filters from the client as authority. `backend/tests/isolation/` must pass before any commit that touches `db/`, `acl/`, `rag/`, `api/` or `agents/`.
3. **No SSRF, no arbitrary paths.** Every user-supplied URL goes through `core/ssrf.py`. File paths from users are never opened, except under `GGUF_ALLOWED_DIRS` in self-host mode.
4. **Secrets**: API keys and OAuth tokens are stored only through `core/crypto.py` (AES-256-GCM envelope). Never log them and never return them to the client (last 4 characters only).
5. **LLM calls only via `llm.gateway.LLMGateway`.** No direct `openai.AsyncOpenAI(...)` elsewhere. Every call passes `CallCtx` (org, user, session, agent role, `contains_pii`).
6. **Prompts are files** in `backend/app/agents/prompts/*.md`, composed by `agents/composer.py` in order L0 → L4. L0 (platform integrity) is never user-editable. Untrusted text (CVs, docs, candidate speech) is always wrapped in `<documents untrusted="true">` / `<candidate>` tags.
7. **Domain knowledge is data.** Embedded-systems content lives in `seed/role_packs/*.yaml`. Never hardcode competencies or questions in Python or TS.
8. **No emotion, face or biometric analysis libraries.** It is prohibited for recruitment in the EU, and `tests/test_banned_imports.py` enforces it.
9. **Verify library APIs against the installed version** (`uv pip show`, the package source in `.venv`, official docs) before using them. Pipecat, LangGraph, Qdrant client, Docling and Next.js change fast. Don't code from memory. Pin exact versions in `uv.lock` / `package-lock.json`.
10. **Licenses**: don't add AGPL/GPL/SSPL/RSAL dependencies to `backend/` or `frontend/` (e.g. no PyMuPDF, no MinIO server code, no Redis ≥7.4 server). GPL services (Piper) run as separate optional containers only.

## Conventions
- Python 3.12, `uv`, `ruff` (lint + format), `mypy --strict` on `app/core`, `app/llm`, `app/rag`, `app/acl`, `app/agents`. Async everywhere in the API; sync CPU work in Celery tasks.
- SQLAlchemy 2.0 typed models; Alembic migrations for **every** schema change (autogenerate, then review by hand). RLS policies live in migrations.
- Pydantic v2 schemas for every API in/out; errors are RFC 7807 problem+json.
- Frontend: TypeScript strict, generated API types (`npm run gen:api`), TanStack Query, shadcn/ui, no `any`.
- Logging: structlog JSON with `request_id`, `org_id`, `user_id`, `session_id`. Never log CV text, transcripts or keys at INFO.
- Tests: pytest + pytest-asyncio + testcontainers (Postgres, Qdrant, Valkey). Use `FakeLLM` for agent tests. Playwright for E2E.

## Commands
```
make env               # create .env with random secrets (once)
make up / down / logs  # docker compose --profile core
make test              # backend unit + integration (Docker) tests, frontend typecheck
make test-unit         # fast, no Docker
make lint              # ruff, ruff format --check, mypy, OpenAPI drift, eslint, tsc
make openapi           # regenerate backend/openapi.json + frontend/src/lib/api/schema.d.ts after API changes
make migrate | seed | create-owner | e2e | selftest   # arrive in Phases 1/2/1/6/3
```

## Definition of done (every task)
Code + migration + tests + docs updated, then `make lint test` passes. Isolation suite green. No new warnings. Commit message in imperative mood, scoped (`rag: add exact-identifier sparse boost`).

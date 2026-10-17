# ADR 0001: Architecture baseline and Phase 0 decisions

- Status: accepted
- Date: 2026-10-17
- Context: Phase 0 (foundation) of `PROMPT.md`. `docs/ARCHITECTURE.md` remains authoritative; this ADR
  records the concrete choices made while implementing it and the places where reality differed from the spec.

## Decisions

1. **Runtime versions.** Python 3.12 in containers (`python:3.12-slim-bookworm`); the package supports
   3.12–3.13. Node 22 LTS. Next.js 16.4 (App Router, `cacheComponents` and `partialPrefetching` as generated
   by `create-next-app`), React 19.3, Tailwind 4. FastAPI 0.143, SQLAlchemy 2.1, Celery 5.6, Qdrant 1.19.2,
   SeaweedFS 4.48, Valkey 8.1, Postgres 16. Exact versions are locked in `backend/uv.lock` and
   `frontend/package-lock.json`.
2. **Backend packaging.** `uv` manages dependencies. Docker images install only dependencies into
   `/opt/venv` and run the code from `/app` via `PYTHONPATH`, so code changes never invalidate the
   dependency layer. The app is built by a factory (`uvicorn app.main:create_app --factory`) with no
   import-time side effects. The Celery factory lives in `workers/config.py`; `workers/celery_app.py` is
   only the CLI entrypoint that reads the environment.
3. **Configuration** is one `pydantic-settings` class declaring every variable from ARCHITECTURE §20 and
   validating it at startup: the master key must decode to 32 bytes, the JWT secret must be ≥32 characters
   and not a placeholder, the DB URL must use asyncpg, CORS rejects `*`, production requires https, and
   `GGUF_ALLOWED_DIRS` requires self-host mode. `make env` generates `.env` with random secrets; nothing
   secret is committed.
4. **Errors** are RFC 7807 `application/problem+json` with a `request_id`. Unhandled exceptions are converted
   by the outermost middleware so even 500s carry `X-Request-ID`; validation errors never echo input.
5. **Logging and metrics.** structlog JSON with secret-key redaction (`token` is sensitive, `tokens_*` and
   `token_count` are usage numbers and are kept). Prometheus metrics are labelled by **route template**
   (unmatched paths collapse to `<unmatched>`) to keep label cardinality bounded.
6. **Readiness.** `/readyz` checks Postgres, Valkey, Qdrant and S3 concurrently with a timeout. It returns
   503 + per-dependency detail when degraded and hides error messages in production. `/healthz` is
   liveness only.
7. **Database roles.** A compose init script creates `app_owner` (owns the schema, used by migrations) and
   `app_rw` (runtime, not the owner) so Phase 1 Row-Level Security applies to the application role.
8. **Object storage.** SeaweedFS is the dev S3 gateway; its entrypoint builds a single S3 identity from
   the environment, and auth is enforced (a wrong secret gets 403). The code stays S3-generic (boto3).
9. **Frontend fonts are bundled** (`geist` npm package via `next/font/local`) instead of
   `next/font/google`. Reason: the self-host/offline profile must build without internet access. Verified by
   building with the proxy disabled.
10. **shadcn/ui components are vendored by hand** (`button`, `card`, `badge`) following shadcn's
    new-york/Tailwind-v4 conventions, with `components.json` configured. Reason: the build sandbox's network
    policy blocks `ui.shadcn.com`. On a normal machine, `npx shadcn@latest add <component>` works as usual.
11. **Dependency audit policy.** Production dependencies are blocking in CI (`pip-audit`, `npm audit
    --omit=dev --audit-level=high`). Dev-only npm advisories are reported but not blocking: at the time of
    writing, `eslint-config-next@16.4.2 → fast-glob → micromatch → braces@3.0.3` matches GHSA-vfj7-8cjw-p6xm and
    **no patched `braces` release exists**. It affects lint tooling only, not the shipped bundle. Revisit
    when a fixed release appears.
12. **Starlette TestClient uses `httpx2`** (Starlette 1.8 deprecates `httpx` for its test client); the app
    itself keeps `httpx` for outbound HTTP.
13. **Corporate TLS proxies.** Both Dockerfiles accept an optional BuildKit secret `extra_ca` (a CA bundle)
    so images build behind TLS-intercepting proxies without baking the CA into layers.

## Consequences

- Every later phase adds its env vars to the existing `Settings` (they are already declared) and its
  services to existing compose profiles.
- The OpenAPI snapshot (`backend/openapi.json`) and generated TS types are drift-checked in CI; API changes
  require `make openapi`.

# Build progress

## Phase 0: Foundation ✅ (2026-10-17)

### Delivered
- **Backend** (`backend/`): FastAPI app factory; validated settings for every env var in ARCHITECTURE §20;
  structlog JSON logging with secret redaction; request-id middleware; RFC 7807 errors; `/healthz`,
  `/readyz` (Postgres, Valkey, Qdrant, S3; 503 when degraded), `/metrics` (Prometheus, route-template
  labels); Celery app with `ingest`, `grade`, `sync`, `default` queues and a `system.ping` task; OpenAPI
  export + committed snapshot; multi-stage Dockerfile (`dev`, non-root `runtime`).
- **Frontend** (`frontend/`): Next.js 16 App Router, TypeScript strict, Tailwind 4, shadcn/ui conventions,
  TanStack Query, typed API client generated from OpenAPI (`openapi-typescript` + `openapi-fetch`),
  validated public env, navigation shell, `/healthz` system-status page; Dockerfile (`dev`, standalone `runtime`).
- **Infra**: `docker-compose.yml` (`core` profile: postgres, valkey, qdrant, seaweedfs + bucket init,
  backend, worker, beat, frontend; all ports on 127.0.0.1); Postgres init creating `app_owner`/`app_rw`;
  `make env` secret generator; `Makefile`; GitHub Actions CI (backend, frontend, security).
- **Docs**: `docs/adr/0001-architecture-baseline.md`, this file, `docs/BACKLOG.md`.

### Acceptance results (run in the build sandbox)
| Criterion | Result |
|---|---|
| `make up` → all core containers healthy | ✅ postgres, valkey, qdrant, seaweedfs, backend, worker healthy; s3-init exited 0; beat + frontend up |
| `curl /readyz` → all deps ok | ✅ HTTP 200, `{"status":"ok"}`, all 4 checks ok. Stopping Qdrant → HTTP 503 `degraded` with the Qdrant error; restart → 200 |
| Celery `ping` round-trips (test) | ✅ `tests/integration/test_celery_ping.py` (testcontainers Valkey + in-process worker) and live against the compose `worker` container |
| CI workflow passes | ✅ Every CI step run locally: ruff, ruff format, mypy --strict, OpenAPI drift, pytest (36 unit + 4 integration), eslint, tsc, `check:api`, `next build`, gitleaks (0 leaks), pip-audit (0), `npm audit --omit=dev` (0). Workflow validated with actionlint 1.7.12. First run on GitHub will happen on the first push to `main` / first PR |
| Frontend talks to backend | ✅ Headless Chromium loads `/healthz`, renders 4 dependency rows with "Ready" and no console errors; shows "Degraded" when Qdrant is down |
| Offline frontend build | ✅ `next build` succeeds with the network proxy disabled (bundled fonts) |

### Known issues / notes
- Dev-only npm advisory (braces via eslint-config-next) has no upstream fix; reported, not blocking (ADR 0001 §11).
- `frontend/AGENTS.md` is generated and maintained by `next dev` (Next.js agent rules); keep it committed.
- `make migrate`, `make seed`, `make create-owner`, `make e2e`, `make selftest` print the phase they arrive in and exit 1.

### Credentials needed later (none for Phase 0)
- Phase 1: SMTP (or use the dev log/Mailpit fallback).
- Phase 2: at least one LLM provider key for `@pytest.mark.live` tests (`LIVE_OPENAI_COMPAT_BASE_URL/KEY/MODEL`).
- Phase 4: Google OAuth client + Picker key, Microsoft Entra app, Dropbox app (live tests only).
- Phase 7: TURN server for testing behind NAT; optional `GROQ_API_KEY` for remote STT.

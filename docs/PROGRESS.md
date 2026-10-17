# Build progress

## Phase 2: LLM Gateway ✅ (2026-10-17)

### Delivered
- **Security primitives**: envelope encryption (`app/core/crypto.py`, per-org data keys in `org_keys`), SSRF guard
  with connect-time DNS pinning (`app/core/ssrf.py`).
- **Gateway** (`app/llm/`): OpenAI-compatible and Fake providers; fallback chains, retries with jitter, TTFT and
  idle timeouts, cancellation, Valkey-backed RPM/RPD budgets and circuit breaker, structured-output ladder,
  usage ledger (`usage_events`, append-only).
- **Presets** (`app/llm/presets.yaml`, 16 providers, data not code), live model discovery, **capability probe**
  with honest quality tiers and warnings.
- **Policy**: org AI policy (`/api/admin/org-policy`), per-user permissions, PII protection from providers that may
  train on data, RLS privacy for personal profiles and routes.
- **API** `/api/llm`: presets, profiles CRUD (write-only keys), probe, try-it, discover, routes.
- **Frontend** `/app/llm`: add-model wizard (presets, key, live model list, share with org, no-training override),
  model cards (status, tier, capabilities, TTFT, tok/s, warnings), try-it dialog, per-role fallback-chain
  editor; admin "AI policy" tab. Prettier added.

### Acceptance results
| Criterion | Result |
|---|---|
| Streaming; 429 → fallback; circuit opens after 5 failures; TTFT timeout fallback; cancellation closes stream; JSON ladder recovers malformed JSON; PII policy block | ✅ `tests/unit/test_gateway.py` (in-process) + `tests/integration/test_llm.py` (real HTTP SSE against a fake OpenAI server, real Postgres/Valkey): `test_routing_falls_back_and_records_usage`, `test_circuit_breaker_opens_after_repeated_failures`, `test_candidate_data_never_goes_to_training_providers` |
| API key never in any response or log | ✅ `test_api_key_is_encrypted_and_never_returned` (checks responses, the raw DB bytes and captured logs) |
| Live test against a real provider | ⏭️ `tests/live/test_live_llm.py` is ready; skipped here (no key in the sandbox, and Groq/OpenRouter hosts are blocked by its network policy). Run with `LIVE_OPENAI_COMPAT_*` set |
| SSRF | ✅ 34 unit cases (private/loopback/link-local/CGNAT/metadata/IPv4-mapped/NAT64, decimal and hex IPs, DNS rebinding) + API-level rejection |
| Browser walkthrough | ✅ add model via wizard → models discovered → probed → rated Excellent → try-it chat → routing saved → AI policy tab |

Bug found by the browser walkthrough and fixed: concurrent CSRF fetches could leave the cached token out of sync
with the cookie (one spurious 403 on login, hidden by the retry). The fetch is now shared, with a regression test.

Totals: backend 96 unit + 39 integration tests, frontend 8 Vitest tests.


## Phase 1: Identity, tenancy, RBAC, RLS, audit ✅ (2026-10-17)

### Delivered
- **Data model + migration**: organizations, users, user_policies, groups, group_members, user_invites,
  refresh_tokens, audit_logs. FORCE RLS tenant policies on every table. Append-only audit log for the runtime role.
  SECURITY DEFINER auth lookups (ADR 0002).
- **Auth API** (`/api/auth`): csrf, signup (pending), register-org (opt-in), login (multi-org aware), refresh
  rotation with reuse detection, logout, me, password change (ends other sessions), invite preview/accept. Rate
  limits. Argon2id.
- **Owner API** (`/api/admin`): members list/search/approve/reject/suspend/reactivate/delete/role, per-user
  policies (voice, coding, prompt editing, own LLM keys, monthly interview quota), groups + membership, invitations,
  audit log with cursor pagination, organization rename. Last-owner protection.
- **Bootstrap**: `OWNER_BOOTSTRAP_EMAIL` first-run invite; `make create-owner` CLI.
- **Infra**: `migrate` one-shot compose service (runs before the backend), Mailpit for dev email (UI :8025).
- **Frontend**: login (org picker), signup, pending, invitation acceptance, dashboard, account (password change),
  admin console (members, permissions dialog, groups, invitations with copyable link, audit log, organization).
  Typed API client with CSRF handling and a single shared session refresh on 401. Vitest suite.

### Acceptance results
| Criterion | Result |
|---|---|
| Pending users get 403 on member APIs; approved users pass | ✅ `test_signup_pending_until_approved` |
| Members get 403 on every `/api/admin/*` route | ✅ `test_member_cannot_use_admin_api` |
| RLS: `app_rw` with org A context sees only org A; without context sees nothing; cross-tenant writes rejected; context ends at commit | ✅ `tests/integration/test_rls.py` (raw psycopg, no ORM) |
| Refresh-token reuse revokes the whole family (+ audit entry) | ✅ `test_refresh_rotates_and_reuse_revokes_family` |
| Audit rows for approve/suspend/policy change | ✅ `test_lifecycle_is_audited` |
| Extra coverage | Owners cannot read or modify another org's users (404); logout and suspension invalidate copied access tokens; rate limiting; invite single-use and revocation; last-owner protection; bootstrap idempotency; CLI |
| Real browser walkthrough (compose stack + Chromium) | ✅ bootstrap invite → owner joins → member requests access → pending page → owner approves → member dashboard; member blocked from admin; audit shows `user.approved`; invitation email delivered to Mailpit |

Totals: backend 42 unit + 29 integration tests, frontend 7 Vitest tests. Lint (ruff, mypy --strict, eslint, tsc) clean.


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

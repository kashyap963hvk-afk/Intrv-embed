# Build progress

## Phase 5: Role packs, CV intelligence, question generation ✅ (2026-10-20)

### Delivered
- **Role packs** (migration `b2f6fa44ead6`): versioned YAML packs with anchored competencies, weights per
  role variant, seed questions, practical tasks and behavioral questions. Built-in packs are seeded per org,
  read only, and can be forked. Org packs are owner-managed; private packs belong to their creator.
- **Role Builder agent**: drafts a pack from a role description (optionally grounded in knowledge spaces),
  repairs weights, validates the spec and diffs it against another pack.
- **CV intelligence**: structured profile with contact details encrypted, a blind summary scrubbed of name and
  contact details, and an evidence index the question generator must cite. Optional JD gap matrix.
- **Question generation** (LangGraph): plan from weights × JD gaps × CV evidence → parallel grounded generation
  per competency → critic → dedupe → top-up from the question bank. Model output is re-checked in code.
  Runs in the background with live progress; edit, pin, reorder, regenerate one question; Markdown/PDF/DOCX export.
- **Frontend**: `/app/role-packs` (list, form + YAML editor with live validation, weight sliders with normalise,
  Role Builder dialog, draft editor) and `/app/questions` (new-set form, live progress, set detail with blind
  profile, generation notes, editable question cards, exports).

### Acceptance results
| Criterion | Result |
|---|---|
| Unit + contract tests | ✅ 151 unit tests; 4 question integration tests with FakeLLM scripts; isolation suite 21/21 |
| Frontend checks | ✅ eslint, tsc, Vitest (105), prettier, `next build` |
| Browser walkthrough | ✅ Headless Chromium: packs list, built-in pack editor (read only, Form/YAML tabs), questions list and the new-set dialog render with no console errors |

### Notes
- Self-healing: the critic is skipped and the set topped up from the question bank when a model call fails; a
  failed set keeps a readable reason and can be retried.
- In this sandbox the backend image must be built with `--network host` and the proxy CA as the `extra_ca`
  build secret (see the Dockerfile header).

## Phase 4: Enterprise connectors and sync ✅ (2026-10-18)

### Delivered
- **Accounts and sources** (migration `6ce88a15e833`, ADR 0006):
  - Credentials are encrypted per org. Accounts are private to the member; org-level accounts are for owners
    only.
  - Sources import a selection into a space once or keep it in sync, and run as their creator.
- **Connectors**:
  - S3-compatible (AWS, R2, B2, Wasabi, Spaces, Ceph, GCS HMAC, SeaweedFS, MinIO) and Azure Blob, both
    confined to a root prefix.
  - Google Drive: per member through OAuth `drive.file` and the Picker; org-wide through the service
    account, limited to shared root folders. Docs, Sheets and Slides are exported.
  - OneDrive/SharePoint (Graph delta) and Dropbox (cursors).
  - URL (SSRF-guarded, robots.txt).
- **OAuth**: PKCE; single-use state bound to the user; tokens stored only encrypted and refreshed
  automatically; provider host allow-list.
- **Sync**:
  - SHA-256 versioning with vector swap and old-original cleanup; remote deletions propagate.
  - Per-file commits (one bad file fails alone); exponential backoff capped at 24 h; email to the creator on
    the first failure; Valkey lock.
  - Celery `sync.source`, plus a beat task (`sync.due`) every minute that reads due ids through a SECURITY
    DEFINER function.
- **API**: `/api/connectors/*` (kinds, accounts, test, browse, picker token, OAuth start/callback, service
  account, org Drive) and `/api/spaces/{id}/sources`, `/api/sources/{id}` (update, sync now, delete with or
  without its documents).
- **Frontend**:
  - The "+" dialog gains Google Drive (Picker), Org Drive, S3 / Cloud bucket, OneDrive / SharePoint, Azure
    and Dropbox tabs, each with connect/test, a folder browser with multi-select and "import whole folder",
    and a Keep-in-sync interval. The Link tab can keep a page in sync.
  - A Sources panel per space shows status, last result, and Sync now / interval / delete.
  - An OAuth popup landing page, and an admin Organization Google Drive card.

### Acceptance results
| Criterion | Result |
|---|---|
| S3 integration against SeaweedFS: import, modify (new version, old vectors gone), delete (vectors and objects gone) | ✅ `test_s3_import_modify_delete_cycle`; Azure Blob is checked the same way against Azurite |
| Contract tests for Drive/Graph/Dropbox with recorded HTTP (respx) | ✅ 14 tests: Drive folders + exports + org-root confinement, Graph full listing → delta → expired link, Dropbox cursor → reset, token refresh, size caps, foreign pagination hosts refused |
| Browse never returns items outside the configured root/prefix | ✅ API and unit tests (`../` refused, sub-folder ids open correctly, look-alike prefixes stay inside) |
| Isolation | ✅ accounts and sources invisible and unusable to colleagues and owners; members cannot see or use org-level storage; OAuth state cannot be completed by another member |
| Deployed path | ✅ API → Celery `sync.source` → ingest, and beat `sync.due` picking up an overdue source |
| Browser walkthrough | ✅ connect an S3 bucket with Test/Save, browse into sub-folders, import the whole folder with Keep in sync, both documents READY, Sources panel shows the result; unconfigured providers explain which env vars they need; no console errors |

Totals: backend 133 unit + 67 integration tests; frontend 56 Vitest tests. Lint, mypy --strict, eslint, tsc and
prettier are clean.

### Bugs found and fixed while testing
- Azure ranged download needed an explicit offset (found by Azurite).
- An unexpected error could leave a source stuck in SYNCING; it now always fails cleanly, and the due scan
  re-picks runs that crashed.
- Expired ORM objects after per-file rollbacks (async sessions cannot lazy-load); state is now reloaded.
- Browsing a sub-folder of a bucket with a root prefix doubled the prefix (found by the frontend build).
- The connector tab bar overflowed its container.

### Known issues / notes
- Live Google, Microsoft and Dropbox flows need real app credentials (DEPLOYMENT.md §4.3); they are covered by
  recorded contract tests only.
- SeaweedFS in the dev stack has few volume slots, so extra buckets may fail to allocate; use prefixes in the
  existing bucket for demos.

## Phase 3: Knowledge spaces, ingestion, hybrid RAG, isolation suite ✅ (2026-10-18)

### Delivered
- **Data model + RLS** (migration `0be01dc63975`):
  - `knowledge_spaces` (personal / org), `space_grants` (user or group, READ / CONTRIBUTE, expiry),
    `documents`, `document_chunks` (parent/child).
  - SECURITY DEFINER `app_space_permissions()` shared by RLS and the API (ADR 0004).
- **Ingestion** (`app/rag/parse.py`, `chunk.py`, `app/knowledge/ingest.py`, Celery `ingest.document`):
  - PDF, DOCX, PPTX, XLSX, HTML, Markdown/text/code and images (RapidOCR); Docling when installed.
  - Hidden-text and prompt-injection risk flags.
  - Structure-aware parent/child chunks.
  - Zip unpacking with bomb guards; SHA-256 de-duplication; URL import through the SSRF guard.
- **Retrieval** (`app/rag/`):
  - Dense embeddings (TEI / fastembed / OpenAI-compatible) plus own BM25 sparse vectors and an exact-identifier
    prefetch, fused with RRF, then an optional cross-encoder reranker.
  - Results are re-checked through RLS before any text is returned.
- **API**:
  - `/api/spaces`, upload / paste / URL, document get / download (presigned) / reindex / delete, and
    `/api/search`.
  - `/api/events` (SSE live status, filtered by readable spaces).
  - Owner `/api/admin/spaces` + grants, and `/api/system/selftest`. `make selftest` runs the same checks in the
    CLI.
- **Frontend**:
  - `/app/knowledge`: spaces, documents table with live status and risk badges, and the **"+" Add** dialog
    (Upload drag-and-drop / Paste / Link, doc-type auto-detect).
  - `/app/knowledge/search` playground with highlighted matches and citations.
  - Admin "Spaces" tab with a grants editor, plus a self-test card.
- **Ops**:
  - `models` compose profile (TEI 1.9.4 for bge-m3 and bge-reranker-v2-m3) and a shared model cache volume.
  - OCR in the image without system packages.
  - `S3_PUBLIC_ENDPOINT_URL` and `.env.example` retrieval/ingestion settings.
- **Isolation suite** (`make test-isolation`, marker `isolation`, 17 tests): personal privacy against
  colleagues, owners and other orgs; immediate revocation; expired grants; guessed UUIDs; presigned links;
  SSE; member deletion purge; zero leakage in RAG eval; 200-combination permission fuzz against an independent
  oracle.
- **New goal recorded: interview integrity / anti-cheating** (ADR 0005, ARCHITECTURE §15.1, scheduled in Phases
  6–8). Browser, answer, audio and in-browser camera (presence + gaze-away) signals; the agent reacts with
  neutral reminders and personal deep-dive probes; the human reviewer gets a timeline. No emotion, identity or
  automatic verdicts.

### Acceptance results (run in the build sandbox)
| Criterion | Result |
|---|---|
| Upload → parse → chunk → embed → index → search with citations | ✅ PDF/DOCX/MD/zip/paste via API and in the browser; live status Pending → Parsing → Ready over SSE |
| Hidden-text CV flagged, hidden text never indexed | ✅ `hidden_text` + `injection_patterns` flags; search never returns the hidden sentence |
| Isolation suite | ✅ 17/17, including the 200-case permission fuzz and SSE privacy over a real HTTP server |
| RAG golden eval (≥40 queries, recall@6 ≥ 0.8, leakage 0, register-name hits table chunk) | ✅ 49 queries, recall@6 = 1.000, leakage 0, 3/3 register queries hit the table chunk (lexical test embedder) |
| Real Celery worker path | ✅ `test_ingest_worker.py` and the compose `worker` container |
| `make selftest` | ✅ embedder, vector store, reranker (disabled), OCR available, Docling not installed |
| Browser walkthrough | ✅ owner uploads 3 files via "+", sees Ready + risk badges, searches, creates an org space and grants READ to a member; the member sees the space but not the owner's private CV (UI and search); no console errors |

Totals: backend 119 unit + 58 integration tests; frontend 29 Vitest tests. Lint (ruff, mypy --strict, eslint, tsc,
prettier) clean.

### Bugs found and fixed while testing
- `INSERT … RETURNING` on spaces failed RLS (policy snapshot); the space manager is now explicit in `space_read`.
- First upload after auto-creating a personal space could 404 (stale ACL cache); creation now bumps the ACL
  version, and SSE refreshes on version change.
- NUL bytes from binary "text" broke Postgres inserts; binaries are rejected and parsers strip control chars.
- Presigned links used SigV2 (rejected by SeaweedFS/R2); now SigV4.
- Deleting a user failed on the policy relationship; cascade fixed.
- `updated_at` lazy-load after flush (MissingGreenlet); models now fetch server defaults eagerly.
- 500 responses lacked CORS headers, so browsers showed "Failed to fetch"; CORS is now outermost.
- Vector/file deletion ran before commit; it now runs after commit.

### Known issues / notes
- Hugging Face is unreachable from the build sandbox, so tests and the sandbox stack use the lexical `hash`
  embedder. fastembed/TEI paths are covered by unit wiring and `make selftest`, and need a networked run to
  measure real-model recall.
- URL documents have no downloadable file until their first fetch completes (download returns 404 until then).

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

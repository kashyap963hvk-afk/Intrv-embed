# Deployment: Vercel frontend, backend hosting and every API key you need

## 1. The honest truth about Vercel

Vercel hosts the **Next.js frontend only**. The backend **cannot** run on Vercel serverless:

| Backend need | Vercel serverless |
|---|---|
| Long-lived WebSockets (text interview, Bridge tunnel) | Not supported |
| WebRTC media (voice worker) | Not supported |
| Celery workers (ingestion takes minutes) | Function time limits |
| Qdrant, Postgres, Valkey | Not hosted there (use managed services) |
| Model weights (bge-m3 ~2.3 GB, Whisper, Kokoro, Docling models) | Exceed bundle limits; no GPU |

Also note that **Vercel Hobby (free) is for personal, non-commercial use** under Vercel's terms. If you charge customers, use Vercel Pro.

## 2. Recommended topologies

### A. Cheapest real deployment (≈ $0–10/month, CPU-only, text-first)
| Component | Service | Free tier (verify; tiers change) |
|---|---|---|
| Frontend | **Vercel** (custom domain `app.example.com`) | Hobby (non-commercial) |
| Backend containers (API, worker, voice-lite, TEI-lite or fastembed) | **Oracle Cloud Always Free** Ampere A1 VM, or a Hetzner/Contabo VPS (~€5–8) | Oracle: Arm VM; check the current OCPU/RAM allowance |
| Postgres | Neon or Supabase, or Postgres on the same VM | Small free DB |
| Qdrant | Qdrant Cloud free cluster, or on the VM | ~1 GB free cluster |
| Valkey/Redis | Upstash, or Valkey on the VM | Free tier |
| Object storage | **Cloudflare R2** | 10 GB free, no egress fees |
| LLM | User BYOK in the UI (Gemini AI Studio / Groq / OpenRouter / Cerebras), or a Local Bridge | Free tiers are rate-limited; see §5 |
| STT | Groq Whisper (BYOK) or faster-whisper `small` int8 on CPU | |
| TTS | Kokoro on CPU, or browser speechSynthesis | |

### B. Production / campus on-prem (offline-capable, GPU)
One Linux box with an NVIDIA GPU (≥12 GB VRAM recommended) running `docker compose --profile core --profile gpu --profile voice --profile coding up -d`, with models from a local disk or pendrive (`GGUF_MANAGED`). The frontend runs on Vercel or as a container on the same box (`frontend` service, `next start`). Nothing leaves the building if every LLM profile is local.

### C. Scale-out cloud
Kubernetes (k3s is fine) with separate deployments: api (2+ replicas), worker-ingest, worker-grade, voice-worker (GPU node pool), inference (TEI on GPU), coturn (host network, public IP). Managed Postgres, Qdrant and Valkey.

## 3. Domain and cookie setup (do not skip)

Auth uses **httpOnly cookies**. Browsers block third-party cookies, so `my-app.vercel.app` plus `api.my-vps.com` **breaks login** in Safari, and increasingly in Chrome.

- Use **one registrable domain**: `app.example.com` (Vercel custom domain) and `api.example.com` (backend). Cookies are set with `Domain=.example.com; Secure; HttpOnly; SameSite=Lax`.
- `CORS_ALLOWED_ORIGINS=https://app.example.com` with credentials enabled on the backend.
- Do **not** proxy the API through Vercel rewrites. WebSockets and long SSE streams do not work reliably through it.

## 4. Environment variables

### 4.1 Vercel (frontend project settings → Environment Variables)
| Variable | Example | Required |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://api.example.com` | Yes |
| `NEXT_PUBLIC_WS_BASE_URL` | `wss://api.example.com` | Yes |
| `NEXT_PUBLIC_APP_URL` | `https://app.example.com` | Yes |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | `xxxx.apps.googleusercontent.com` | For Google Drive "+" |
| `NEXT_PUBLIC_GOOGLE_PICKER_API_KEY` | `AIza...` (restricted to your domain + Picker API) | For Google Drive "+" |
| `NEXT_PUBLIC_GOOGLE_APP_ID` | Google Cloud **project number** | For Google Drive "+" |

No secrets go on Vercel. TURN credentials, OAuth tokens and LLM keys are all served or used by the backend.

### 4.2 Database roles (required for Row-Level Security)

The app uses two Postgres roles (created automatically by `infra/postgres/init/01-roles.sh` in Docker):

| Role | Used by | Needs |
|---|---|---|
| `app_owner` | migrations (`DATABASE_MIGRATION_URL`) | owns the schema, **`BYPASSRLS`** (its SECURITY DEFINER auth-lookup functions must see across tenants) |
| `app_rw` | the API and workers (`DATABASE_URL`) | `NOBYPASSRLS`, not the table owner, so every query is confined to one organization |

On a managed Postgres (Neon, Supabase, RDS…) create both roles yourself with the SQL from that script.
**Check that your provider lets you grant `BYPASSRLS`**; without it, login cannot work. Supabase's `postgres`
role has it; on others, use the provider's admin role to `ALTER ROLE app_owner BYPASSRLS`.

### 4.3 Backend (VPS / container secrets)
| Variable | How to get it | Required |
|---|---|---|
| `MASTER_KEY_BASE64`, `JWT_SECRET` | `openssl rand -base64 32` (each) | Yes |
| `DATABASE_URL`, `DATABASE_MIGRATION_URL` | Neon/Supabase connection strings, or self-hosted | Yes |
| `VALKEY_URL` | Upstash `rediss://...` or `redis://valkey:6379/0` | Yes |
| `QDRANT_URL`, `QDRANT_API_KEY` | Qdrant Cloud console, or `http://qdrant:6333` | Yes |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` | Cloudflare R2 → API token with Object Read & Write (region `auto`) | Yes |
| `OWNER_BOOTSTRAP_EMAIL` | Your email | Yes (first run) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` | Any SMTP (e.g. a transactional email provider's free tier) | For invites and approvals |
| `GOOGLE_PICKER_API_KEY` | Google Cloud → Credentials → API key restricted to the Picker API and your app's HTTP referrer (same value as `NEXT_PUBLIC_GOOGLE_PICKER_API_KEY`) | Google Drive Picker |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google Cloud Console → APIs & Services → Credentials → OAuth client (Web). Enable **Google Drive API** and **Google Picker API**. Redirect URI `https://api.example.com/api/connectors/oauth/google/callback` (and `/api/auth/oidc/google/callback` for SSO). Scope: `drive.file` | Google Drive / SSO |
| `GOOGLE_SERVICE_ACCOUNT_JSON_BASE64` | Google Cloud → IAM → Service accounts → key (JSON), base64-encoded. In the app, Admin → Spaces → *Organization Google Drive* shows the SA email: share the folders with it (Viewer) and register their folder ids | Owner org-wide Drive sync |
| `MS_CLIENT_ID`, `MS_CLIENT_SECRET`, `MS_TENANT` | Microsoft Entra → App registrations; redirect `https://api.example.com/api/connectors/oauth/microsoft/callback`; delegated permissions `Files.Read.All`, `Sites.Read.All`, `User.Read`, `offline_access` | OneDrive/SharePoint |
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET` | Dropbox App Console (scoped app; permissions `account_info.read`, `files.metadata.read`, `files.content.read`); redirect `https://api.example.com/api/connectors/oauth/dropbox/callback` | Dropbox |
| `TURN_URLS`, `TURN_USERNAME`, `TURN_CREDENTIAL` (or `TURN_SHARED_SECRET` for coturn REST auth) | Self-host coturn on a VM with a public IP (UDP 3478 + relay ports), or a managed TURN provider | Voice behind corporate NATs |
| `GROQ_API_KEY` | console.groq.com | Only if STT uses Groq |
| `LANGFUSE_*`, `OTEL_EXPORTER_OTLP_ENDPOINT` | Self-hosted or cloud | Optional |

**LLM API keys are not environment variables.** Each user (or the owner, for org-shared profiles) pastes keys in **/app/llm**. They are encrypted per org. Optional `SEED_LLM_PROFILES_YAML` can pre-create org profiles for demos.

## 5. LLM provider quick reference (presets ship in `backend/app/llm/presets.yaml`)

| Provider | Base URL | Notes |
|---|---|---|
| Google Gemini (AI Studio) | `https://generativelanguage.googleapis.com/v1beta/openai/` | Free tier available; **free-tier data may be used to improve Google products**, so it is blocked for candidate PII by default policy. Text + vision. |
| Groq | `https://api.groq.com/openai/v1` | Very low TTFT, good for voice; free tier rate limits; Whisper STT too. |
| OpenRouter | `https://openrouter.ai/api/v1` | `:free` models: ~20 req/min and 50 req/day (1,000/day after a one-time $10 credit purchase). A 45-min interview takes 60–120 calls, so free tier = demos only. |
| Cerebras | `https://api.cerebras.ai/v1` | Very fast inference; free tier. |
| Mistral | `https://api.mistral.ai/v1` | Free experimentation tier. |
| OpenAI / Anthropic / DeepSeek / Together | their OpenAI-compatible endpoints | Paid; best quality per call. |
| Ollama (self-host / bridge) | `http://localhost:11434/v1` | Via Bridge in cloud mode. |
| LM Studio (bridge) | `http://localhost:1234/v1` | Via Bridge. |
| llama-server (bridge / GGUF_MANAGED) | `http://127.0.0.1:<port>/v1` | Spawned by Bridge from a `.gguf` on a pendrive or disk. |

Model IDs change constantly, so the UI fetches `GET /models` live.

## 6. Deploy on one VPS (step by step)

**Server:** Ubuntu 24.04, 4 vCPU and 8 GB RAM minimum for text interviews (16 GB with voice; a GPU only if you
self-host models). Open ports 80 and 443 (TCP and UDP), plus UDP 3478 and 49160–49200 if you enable voice.

**DNS:** create A records pointing at the server for `api.example.com` and `files.example.com`, plus
`app.example.com` if the frontend runs on the server instead of Vercel.

```bash
# 1. Docker and the code
curl -fsSL https://get.docker.com | sh
git clone <your fork> intrv && cd intrv

# 2. Secrets: generates every password and key, then edit the rest
make env
```

Edit `.env`:

| Variable | Value |
|---|---|
| `APP_ENV` | `prod` (HTTPS is then enforced and API docs are off) |
| `API_DOMAIN`, `FILES_DOMAIN`, `ACME_EMAIL` | your domains and an email for Let's Encrypt |
| `APP_DOMAIN` | only if the frontend runs here (see step 4) |
| `PUBLIC_APP_URL`, `CORS_ALLOWED_ORIGINS` | `https://app.example.com` |
| `API_PUBLIC_URL` | `https://api.example.com` |
| `COOKIE_DOMAIN` | `.example.com` (§3) |
| `S3_PUBLIC_ENDPOINT_URL` | `https://files.example.com` (bundled store), or your R2/S3 endpoint |
| `OWNER_BOOTSTRAP_EMAIL` | your email: the first start emails (and logs) an owner invite |
| `SMTP_*`, `MAIL_FROM` | a real SMTP provider (invites, approvals); without it, use the logged link |
| `BACKUP_*` | §7 |

```bash
# 3. Start: core services, Caddy (automatic HTTPS) and nightly backups. Migrations run first, automatically.
make up-prod                              # add PROFILES="voice coding frontend" as needed

# 4a. Frontend on Vercel (recommended): import the repo, root directory `frontend/`, variables from §4.1.
# 4b. Or on this server: cp deploy/caddy/app.caddy.example deploy/caddy/app.caddy, set APP_DOMAIN,
#     then: make up-prod PROFILES="frontend"

# 5. The owner invite link (if no SMTP):
make logs-prod | grep bootstrap_owner_invite

# 6. Health
curl https://api.example.com/readyz
make selftest STACK=prod
```

Then log in as the owner, run **Admin → System self-test**, add an LLM profile in **/app/llm** and press
**Test connection**.

**Updating:** `git pull && make up-prod` (images rebuild, migrations run, containers restart one by one).

**Optional profiles**
- `voice`: set `VOICE_PUBLIC_URL=https://api.example.com` (Caddy forwards `/voice/*` to the worker) and
  `TURN_URLS=turn:api.example.com:3478?transport=udp`. The worker image downloads the Whisper model while it is
  built; or use Groq (`STT_BACKEND=groq`, `GROQ_API_KEY`).
- `coding`: Piston runs candidate code (privileged container; no network inside the sandboxes). Set
  `PISTON_URL=http://piston:2000`.

## 7. Backups and restore

The `backup` service runs every night at `BACKUP_HOUR_UTC`, and `make backup-now` runs it immediately. Each run
writes a folder under the `backups` volume with three files:
- `postgres.dump`: `pg_dump` custom format;
- `qdrant.tar`: one snapshot per collection;
- `objects.tar`: the whole object store (CVs, documents, recordings, diagrams).

Each file is encrypted with `age` when `BACKUP_AGE_RECIPIENT` is set, and copied to `BACKUP_S3_BUCKET` when that
is configured. Local copies are kept for `BACKUP_KEEP_DAYS`. Backups contain candidate personal data, so always
set an age recipient, and keep its private key off the server.

**Restore** on a fresh server. You need the **same `.env`**, because `MASTER_KEY_BASE64` decrypts the stored API
keys and OAuth tokens.

```bash
set -a && . ./.env && set +a
age -d -i backup-key.txt postgres.dump.age > postgres.dump      # same for qdrant.tar.age, objects.tar.age
P="docker compose -f deploy/compose.prod.yml --env-file .env"
$P up -d postgres qdrant seaweedfs && $P run --rm s3-init
$P exec -T postgres pg_restore -U postgres -d intrv --clean --if-exists --no-owner --role=app_owner < postgres.dump
tar xf qdrant.tar && for f in qdrant/*.snapshot; do
  docker run --rm --network intrv-prod_default -v "$PWD/qdrant:/q:ro" curlimages/curl:8.17.0 -sf -X POST \
    -H "api-key: $QDRANT_API_KEY" -F "snapshot=@/q/$(basename "$f")" \
    "http://qdrant:6333/collections/$(basename "$f" .snapshot)/snapshots/upload?priority=snapshot"
done
tar xf objects.tar && $P run --rm -v "$PWD/objects:/restore:ro" --entrypoint aws s3-init \
  --endpoint-url "$S3_ENDPOINT_URL" s3 sync /restore "s3://$S3_BUCKET"
make up-prod
```

Test a restore on a spare machine once a quarter. A backup that has never been restored is only a hope.

## 8. First deploy checklist

1. Buy or choose a domain. Point `api` and `files` at your VPS, and `app` at Vercel (or at the VPS).
2. VPS: follow §6.
3. Vercel: import the repo, set the root directory to `frontend/`, set the §4.1 variables, deploy.
4. Log in as the owner, check `/admin → System self-test` (all green), add an LLM profile → **Test connection**.
5. Upload a sample CV via "+" → generate questions → run a text interview → check the report.
6. Only then enable voice (TURN configured) and invite real candidates, after the consent text is reviewed by whoever handles legal for you.
7. Run `make backup-now` and do one test restore (§7).

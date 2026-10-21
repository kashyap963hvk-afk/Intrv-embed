# Intrv-embed

An agentic, human-like AI interviewer. **Embedded Systems is the default domain, and any role works** through
editable Role Packs. Plug and play any AI: cloud APIs (Groq, OpenRouter, OpenAI-compatible...), your own server, or
a `.gguf` model on a PC or pendrive through the Local Model Bridge.

- **Interviews** run as text or voice. The interviewer plans from the CV, the job description and the role pack,
  asks follow-up questions, and switches between voice and text in the same session.
- **Practical rounds:** a code runner with tests, reviewer findings checked against the code, a whiteboard, and
  datasheet tasks.
- **Grading:** blind and evidence-quoted; the reviewer sees quotes, variance flags and an **advisory** scorecard,
  and a person makes every decision.
- **Integrity signals** (browser and, with consent, in-browser face count and gaze) go to the human reviewer only.
  There is no emotion, identity or demographic analysis.
- **Knowledge:** enterprise RAG over owner and user documents, with per-user access control enforced by
  Postgres RLS.
- **Self-healing:**
  - if the planner fails, the plan is built from the role pack;
  - if no model answers, the engine uses a heuristic controller and the planned questions;
  - an overloaded server answers "busy" and clients retry;
  - a beat task recovers stuck work, and failed grading offers "Grade again".

**Status:** phases 0–10 are built. See [`docs/PROGRESS.md`](docs/PROGRESS.md) for acceptance results and
[`docs/BACKLOG.md`](docs/BACKLOG.md) for what is deliberately left out.

## Try it locally (no API keys needed to start)
```bash
make env          # .env with random secrets
make up           # Postgres, Valkey, Qdrant, SeaweedFS (S3), backend, worker, beat, frontend
make create-owner ORG=acme EMAIL=you@example.com NAME="Your Name"   # then type a password
open http://localhost:3000                                          # sign in, add a model under "AI models"
```
The full walkthrough, including voice, code tasks and a local model, is in [`docs/TESTING.md`](docs/TESTING.md).

## Deploy
[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fkashyap963hvk-afk%2FIntrv-embed&root-directory=frontend&project-name=intrv-embed&env=NEXT_PUBLIC_API_BASE_URL,NEXT_PUBLIC_WS_BASE_URL,NEXT_PUBLIC_APP_URL&envDescription=Where%20your%20Intrv-embed%20API%20runs%20and%20this%20app%27s%20own%20URL&envLink=https%3A%2F%2Fgithub.com%2Fkashyap963hvk-afk%2FIntrv-embed%2Fblob%2Fmain%2Fdocs%2FDEPLOYMENT.md)

Use one VPS with automatic HTTPS (`make up-prod`), with the frontend on Vercel or on the same server. For
everything you need, including keys, DNS and backups, see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Develop
```bash
make lint test       # ruff, mypy --strict, eslint, tsc, OpenAPI drift; pytest unit + integration (Docker)
make test-isolation  # tenant and space isolation suite (required before touching db/acl/rag/api/agents)
make loadtest N=50   # concurrent text interviews (docs/LOADTEST.md)
make e2e             # Playwright browser tests against a running stack
```

## Documents
| File | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Engineering rules (tenant isolation, no placeholders, prompts as files, banned analyses). |
| [`PROMPT.md`](PROMPT.md) | The phase-by-phase build plan with acceptance tests. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System spec: tenancy, RAG, LLM gateway, agents, voice, compliance, API, data model. |
| [`docs/adr/`](docs/adr) | Decisions: engine (0007), bridge (0008), voice (0009), practical rounds (0010), integrity (0005)… |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Vercel + VPS deployment, every key and where to get it, backups and restore. |
| [`docs/TESTING.md`](docs/TESTING.md) | How to test it yourself, from a laptop to real candidates. |
| [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) | Intended use, what the system never does, how scores are produced (generated from code). |
| [`docs/LOADTEST.md`](docs/LOADTEST.md) | Load test results and sizing. |
| [`docs/DOMAIN_PACK_EMBEDDED.md`](docs/DOMAIN_PACK_EMBEDDED.md) | Role Pack schema and the Embedded Systems pack; how to adapt it to any role. |

# Intrv-embed

An agentic, human-like AI interviewer. **Embedded Systems is the default domain, and any role works** through editable Role Packs.
Plug and play any AI: cloud APIs (Gemini, Groq, OpenRouter, OpenAI-compatible...), your own server, or a `.gguf` model on a pendrive.
Enterprise RAG (hybrid search + reranking) over owner- and user-provided documents with strict per-user access control.

The code is built phase by phase with Claude Code from `PROMPT.md`. **Status: Phase 0 (foundation) done**; see [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Quickstart (local)
```bash
make env        # create .env with random secrets
make up         # Postgres, Valkey, Qdrant, SeaweedFS (S3), backend, worker, frontend
open http://localhost:3000/healthz   # system status page; API docs at http://localhost:8000/docs
make lint test  # ruff, mypy, eslint, tsc, pytest (integration tests need Docker)
```

## Files
| File | Purpose |
|---|---|
| [`PROMPT.md`](PROMPT.md) | **Master prompt to give Claude Code.** 11 phases (0–10), each with build steps and acceptance tests. |
| [`CLAUDE.md`](CLAUDE.md) | Hard engineering rules Claude Code loads every session. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Authoritative system spec: tenancy, RAG, LLM gateway, agents, voice, compliance, API, data model. |
| [`docs/AGENT_PROMPTS.md`](docs/AGENT_PROMPTS.md) | Production prompts for every agent (integrity layer, interviewer, controller, planner, grader...). |
| [`docs/DOMAIN_PACK_EMBEDDED.md`](docs/DOMAIN_PACK_EMBEDDED.md) | Role Pack schema + the Embedded Systems pack, and how to adapt it to any role. |
| [`backend/seed/role_packs/embedded-systems.yaml`](backend/seed/role_packs/embedded-systems.yaml) | The default pack: 16 competencies, 6 role variants, practical tasks. |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Vercel + backend hosting + every API key and where to get it. |
| [`docs/GEMINI_DRAFT_REVIEW.md`](docs/GEMINI_DRAFT_REVIEW.md) | 24 problems in the earlier Gemini architecture, and how this design fixes them. |

## Continue the build with Claude Code
```bash
claude "Read CLAUDE.md, PROMPT.md and everything in docs/ (start with docs/PROGRESS.md). Execute the next phase. Stop at the end of the phase, show me the acceptance results, and wait for 'continue'."
```

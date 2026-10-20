# Backlog

Items deliberately left out of a phase (CLAUDE.md rule 1: no stubs; list it here instead).

| Item | Why deferred | Target |
|---|---|---|
| OpenTelemetry tracing (`core/telemetry.py`) and the `observability` compose profile | Needs real spans from LLM/agent code to be useful | Phase 2 (gateway spans), completed in Phase 10 |
| Password reset by email ("forgot password") | Needs a signed, single-use reset token flow + email template; owners can re-invite meanwhile | Phase 10 |
| OIDC single sign-on (Google / Microsoft) | Optional per ARCHITECTURE §3; password + invites cover Phase 1 | Phase 10 |
| Playwright E2E suite (`make e2e`) | Interview flows are covered by API + WebSocket integration tests and a scripted browser walkthrough; the CI-run Playwright suite lands with the production compose profile | Phase 10 |
| `prod` compose profile (Caddy TLS, production images, resource limits, backups) | Production hardening phase | Phase 10 |
| Dark/light theme toggle | Theme follows the OS setting via `prefers-color-scheme`; toggle is cosmetic | Unscheduled |
| Re-enable blocking `npm audit` for dev deps | Waiting for a patched `braces` release (ADR 0001 §11) | When upstream ships a fix |
| Real-model RAG eval run (bge-m3 + bge-reranker-v2-m3 through TEI) with published recall numbers | Hugging Face unreachable from the build sandbox | Phase 10 (or any networked CI run: `EMBED_BACKEND=tei pytest tests/integration/test_rag_eval.py`) |
| Live "document.deleted" SSE event | Lists refresh after the delete call; other open tabs see it on the next poll | Phase 4 (with connector sync events) |
| Third-party identity verification (KYC) for candidates | Face matching is biometric identification; a human compares the start snapshot with the ID instead (ADR 0005) | Unscheduled |
| Template "Test with sample CV" upload | Simulation runs the real controller, policy and interviewer on the pack's first technical topics with scripted answers, without a CV | Phase 10 |
| Waiting up to 60 s for in-flight topic grades before finalising | Finalisation grades any missing topic itself and keeps the latest of duplicate runs, so waiting adds latency without changing the result | Unscheduled |
| Softened candidate feedback export | Reports are interviewer-only for now | Phase 10 |

# Backlog

Items deliberately left out of a phase (CLAUDE.md rule 1: no stubs; list it here instead).

| Item | Why deferred | Target |
|---|---|---|
| OpenTelemetry tracing (`core/telemetry.py`) and the `observability` compose profile | Needs real spans from LLM/agent code to be useful | Phase 2 (gateway spans), completed in Phase 10 |
| Frontend unit test runner (Vitest) | Phase 0 UI is one read-only page; covered by typecheck and a manual browser check | Phase 1 (auth forms) |
| Playwright E2E suite (`make e2e`) | Needs auth and interview flows | Phase 6 |
| `prod` compose profile (Caddy TLS, production images, resource limits, backups) | Production hardening phase | Phase 10 |
| Dark/light theme toggle | Theme follows the OS setting via `prefers-color-scheme`; toggle is cosmetic | Unscheduled |
| Re-enable blocking `npm audit` for dev deps | Waiting for a patched `braces` release (ADR 0001 §11) | When upstream ships a fix |

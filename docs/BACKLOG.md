# Backlog

Items deliberately left out of a phase (CLAUDE.md rule 1: no stubs; list it here instead).

| Item | Why deferred | Target |
|---|---|---|
| OpenTelemetry tracing (`core/telemetry.py`) and the `observability` compose profile | Needs real spans from LLM/agent code to be useful | Phase 2 (gateway spans), completed in Phase 10 |
| Password reset by email ("forgot password") | Needs a signed, single-use reset token flow + email template; owners can re-invite meanwhile | Phase 10 |
| OIDC single sign-on (Google / Microsoft) | Optional per ARCHITECTURE §3; password + invites cover Phase 1 | Phase 10 |
| Dark/light theme toggle | Theme follows the OS setting via `prefers-color-scheme`; toggle is cosmetic | Unscheduled |
| Re-enable blocking `npm audit` for dev deps | Waiting for a patched `braces` release (ADR 0001 §11) | When upstream ships a fix |
| Real-model RAG eval run (bge-m3 + bge-reranker-v2-m3 through TEI) with published recall numbers | Hugging Face unreachable from the build sandbox | Phase 10 (or any networked CI run: `EMBED_BACKEND=tei pytest tests/integration/test_rag_eval.py`) |
| Live "document.deleted" SSE event | Lists refresh after the delete call; other open tabs see it on the next poll | Phase 4 (with connector sync events) |
| Third-party identity verification (KYC) for candidates | Face matching is biometric identification; a human compares the start snapshot with the ID instead (ADR 0005) | Unscheduled |
| Template "Test with sample CV" upload | Simulation runs the real controller, policy and interviewer on the pack's first technical topics with scripted answers, without a CV | Phase 10 |
| Waiting up to 60 s for in-flight topic grades before finalising | Finalisation grades any missing topic itself and keeps the latest of duplicate runs, so waiting adds latency without changing the result | Unscheduled |
| Softened candidate feedback export | Reports are interviewer-only for now | Phase 10 |
| Audio integrity signals: second voice, background whispering, keyboard transients | Needs speaker-embedding and audio-event models (Hugging Face is unreachable from the build sandbox); the engine already accepts the events | Phase 8 (with camera signals) |
| Voice room for the interviewer's own preview | The voice worker authenticates with the candidate's invite token; interviewers preview voice by opening the invite link | Unscheduled |
| Renode MCU simulation for firmware tasks (`RENODE_ENABLED`) | Could not be built and verified reliably in the build environment (ADR 0010); Piston covers host-compiled tasks | Unscheduled |
| Live Piston container check in this sandbox | GHCR blob storage is blocked here; the runner is tested against a Piston-API server using the host gcc | Any networked CI run |
| Owner break-glass (reason, time-box, audit, org toggle) | Adds an RLS read path for owners onto other members' hiring records; built only with the platform owner's explicit sign-off on that trade-off | Awaiting approval |
| Org-wide selection-rate report (AI recommendation vs human decision, per role and month) | Needs an aggregate cross-member read path, same sign-off as break-glass; group-level adverse impact needs self-reported demographics the platform deliberately does not store | Awaiting approval |
| Upload antivirus (ClamAV container) | Uploads are parsed in workers and never served back as HTML; a ClamAV sidecar adds 1 GB RAM and daily signature downloads | Unscheduled |
| Voice: separate "engine link lost" frame instead of `ended` with reason `closed` | The room already treats that reason as a reconnect, not the end of the interview | Unscheduled |
| Voice: count open sockets per session before pausing the clock on a disconnect | When the voice worker's socket closes while the text socket stays open, the clock pauses until the room resyncs (immediately, on switching to text) | Unscheduled |

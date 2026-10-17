# ADR 0003: LLM gateway

- Status: accepted
- Date: 2026-10-17
- Context: Phase 2. ARCHITECTURE §9.

## Decisions

1. **One protocol, no LiteLLM.** Every target (cloud APIs, vLLM, Ollama, LM Studio, llama-server) speaks the
   OpenAI chat-completions protocol, so the gateway uses the official `openai` SDK behind our own
   `ChatProvider` interface. A deterministic `FakeProvider` (scripted, disabled in production) powers tests and
   offline demos.
2. **The OpenAI SDK v3 uses `httpx2`** (the Pydantic org's successor to httpx) rather than `httpx`, so outbound
   clients for user-supplied destinations are built on `httpx2`/`httpcore2`. Internal clients (Qdrant readiness)
   still use `httpx`.
3. **SSRF protection is enforced at connect time.** A custom `httpcore2` network backend resolves the hostname,
   rejects the connection if any address is not globally routable (handling IPv4-mapped and NAT64 forms, decimal
   and hex IP tricks, and DNS rebinding), and connects to the validated address while TLS still verifies the
   hostname. Static URL checks (scheme, credentials, internal names) run on save. `ALLOW_PRIVATE_LLM_ENDPOINTS=true`
   (self-host only) uses an unguarded client.
4. **Envelope encryption.** Each org gets a data key wrapped by `MASTER_KEY_BASE64` (AES-256-GCM, AAD = org id).
   Values are encrypted with AAD = purpose plus row id, so a ciphertext cannot be moved to another row or org. Keys
   are write-only from the API (last 4 characters shown, and only for keys of 8+ characters).
5. **Resilience lives in the gateway**: per-profile RPM/RPD budgets and a circuit breaker (5 failures/60 s →
   open 30 s) are shared through Valkey. It retries with full-jitter backoff only for retryable errors (429, 5xx,
   timeouts, connection) and only before the first token, then falls back along the ordered chain. Time-to-first-
   token and idle timeouts apply. Closing the consumer stream closes the upstream HTTP stream (barge-in).
6. **Structured output ladder** per profile: native `json_schema` → `json_object` → schema in the prompt; then
   `<think>` stripping, fence extraction, `json-repair`, and Pydantic validation; then one corrective retry
   that shows the validation error; then the next profile.
7. **Routing**: an ordered chain per agent role. A user route overrides the org default; with no route, the best
   visible profiles are used (status, then quality tier). Org routes may only reference org-shared profiles.
8. **Privacy of profiles is enforced by RLS**: personal profiles are visible only to their owner (not even to
   org owners); org-shared rows are readable by all members and writable only by owners (`app.role = 'OWNER'`
   in the policy's WITH CHECK).
9. **Policy**: org policy (allowed kinds and presets, member profiles allowed, block training providers for PII)
   plus per-user policy (may add models, allowed profile list). `trains_on_data` comes from presets. Only owners
   may override it. Unknown counts as "may train" when `contains_pii` is set.
10. **Capability probe**: about a dozen small calls. They measure TTFT and throughput and check JSON modes, tools
    and vision (with a generated PNG), plus a 5-question mini-eval. The tier is capped by the parameter count in the
    model name (<10B Basic, <20B Usable, <50B Good).

## Consequences

- `BRIDGE` and `GGUF_MANAGED` profile kinds exist in the schema but are rejected until Phase 9.
- Hugging Face and some provider APIs are unreachable in the build sandbox, so real-provider checks are a
  `live`-marked test run with your own key (`tests/live/test_live_llm.py`).

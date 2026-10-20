# ADR 0007: Live interview engine (text)

- Status: accepted
- Date: 2026-10-20
- Context: Phase 6. ARCHITECTURE §13, §15.1, §16.

## Decisions

1. **The interview is an explicit state machine, not a LangGraph checkpoint.**
   - State (phase, topic index, section clock, follow-up and hint counters, asked questions, recent
     acknowledgements, turn mode, pause time) lives in `interview_sessions.state` (JSONB).
   - One candidate answer is one transaction: the answer, its integrity signals, the interviewer's reply and the
     new state are committed together. A dropped connection or a crashed process never leaves half a turn; the
     client resends an unacknowledged answer, and answers are idempotent by `client_id`.
   - A per-session lock (Valkey `SET NX PX`, process lock if Valkey is unreachable) serialises turns across API
     replicas and browser tabs.
   - LangGraph remains the tool for fan-out work (question generation); a turn loop with strict latency needs
     no graph runtime.
2. **Controller, then code, then the interviewer.**
   - `single_call` (models rated GOOD or better by the probe, or forced by the template): one streamed call that
     starts with `<control>{TurnControl}</control>`. The control block is parsed before any text is forwarded;
     the deterministic policy decides; if the model's own reply fits that decision it streams on, otherwise it
     is discarded and the interviewer is called with the policy's directive.
   - `two_call`: structured controller call, then the interviewer stream with the directive.
   - Two malformed control blocks in a session switch it to `two_call` for good.
   - `policy.decide` owns time (section and total, wrap-up two minutes before the end), follow-up depth, the
     single hint per topic, prompt attacks, meta requests, clarifications, candidate questions and the close.
     The code double-checks prompt attacks with the RAG injection patterns even when the model missed them.
3. **Human-likeness is enforced in code, not only asked for.** `SentenceGate` forwards whole sentences, strips
   Markdown, emojis and praise, stops after the first question or the word limit (90 text, 45 voice). A reply
   without a question gets the planned question appended. Acknowledgements rotate and are not repeated within 5
   turns. Prompt layers are composed L0 → L4 from files; L2 (the template persona) is the only editable layer
   and is linted against L0.
4. **Self-healing paths.**
   - Planner unavailable → a complete plan from the question set and the role pack (`source: "fallback"`).
   - Controller unavailable → heuristic classification. Interviewer unavailable → the plan's own questions.
     An interview never stalls because a provider is down.
   - Grading unavailable → the report is `FAILED` with "Grade again"; any topic whose background grade never
     ran is graded at finalisation; a missing narrative model yields a score-based narrative.
   - `system.heal` (beat, every 5 minutes) uses the SECURITY DEFINER function `app_stuck_work()` to find
     preparation, grading, question sets and documents left behind by dead workers, and live interviews
     abandoned for longer than their duration + 1 hour. Each item is re-dispatched (at most 3 times) or closed
     inside its owner's tenant context.
5. **Grading is blind, evidence-bound and repeated.**
   - Graders see the topic transcript, rubric anchors, the reference pack and the blind CV summary; never the
     name, demographics or integrity data.
   - Every quote is verified against the stored candidate turns in code (wrong turn numbers are corrected,
     invented quotes are dropped). A grade without a verifiable quote is retried once, then stored as "not
     assessed".
   - Finalisation runs `grading_runs` (default 3) runs per topic, takes the median, flags spreads of ≥ 2
     points for human review, and computes the weighted score in code. The model writes only the narrative.
   - The recommendation is labelled advisory; the human decision is recorded and audit-logged.
6. **Candidate access is a capability link.** The invite token (256-bit, stored as SHA-256, expiring) is the
   candidate's only credential: `GET /api/join/{token}`, consent, integrity events and the WebSocket. The
   lookup function returns ids only; everything after it runs in the creator's tenant context. The creator can
   open the same socket with their session cookie (origin-checked) to preview or run the interview.
7. **Integrity (ADR 0005) in the text engine.** Browser events arrive in batches; camera events are refused
   unless the candidate consented to them. Answer-level signals (paste share, typing speed, long silence then
   fluent text, overlap with reference text, prompt attacks) are computed on the server. Unaddressed medium+
   signals produce at most one neutral reminder per section, and two or more turn the next follow-up into an
   integrity probe. Nothing here changes a score.

## API changes against ARCHITECTURE §16

- Candidate integrity events are posted to `POST /api/join/{token}/integrity` (or sent over the socket) rather
  than `POST /api/interviews/{id}/integrity`: the candidate has no account, so the token is the authority.
- Added `POST /api/interviews/{id}/regrade` and `GET /api/interviews/{id}/export?format=pdf|json`.
- `pause` is not exposed: the clock pauses automatically while the candidate is disconnected.

## Consequences

- Turn latency is one model call in the common case (single-call probes) and two on topic changes.
- A turn holds one DB connection while the model streams; pool size bounds concurrent turns per replica.
- Voice (Phase 7) reuses the same engine through the sentence stream; only transport and timers differ.

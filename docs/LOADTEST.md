# Load test: concurrent text interviews

Harness: `loadtest/interviews.py` (`make loadtest N=50`). It drives N complete text interviews at once through
the public HTTP and WebSocket API: create, plan, invite, consent, start, 5 answers, end. The org's model is a
scripted FAKE profile that answers instantly, so the numbers measure the platform (engine, Postgres, Valkey
locks, sockets), **not** an LLM. Add your model's own latency on top.

## Run 2026-10-21 (development stack)

Single `uvicorn --reload` process, Postgres 16, Valkey 8 and Qdrant on the same 4-vCPU container.
This is the worst case: every candidate answers instantly, so all 50 turns are in flight together.

- Concurrent interviews: 50, answers each: 5, think time: ~0 s, wall time: 9.4 s
- Completed: 50/50, turns measured: 250, errors: 0, busy retries: 0

| Stage | p50 ms | p95 ms | max ms |
|---|---|---|---|
| Answer → first interviewer words | 718 | 937 | 1296 |
| Answer → turn complete | 734 | 953 | 1306 |

**Finding fixed by this run:** the first run failed. With 10 + 10 pooled database connections, 50 simultaneous
turns exhausted the pool, because a live turn deliberately keeps one connection for its single transaction
(ADR 0007). Requests then waited 30 s and failed with a 500, or the socket dropped. The fixes:
- pool defaults raised to 20 + 20 per process, and Postgres `max_connections=200`;
- an exhausted pool now returns 503 `busy` with `Retry-After` (HTTP) or a `busy` frame (WebSocket), which
  clients retry, instead of failing.

## Sizing

What sets capacity is the number of **turns in flight**, not the number of interviews. A real candidate takes
30–90 s per answer, and the model 2–8 s per reply, so 50 live interviews have about 3–10 turns in flight on
average. Each turn in flight holds one database connection while the model answers. Keep
`API workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW)` above the burst you expect (for example, a class of
candidates who all start at 10:00), and below Postgres `max_connections`.

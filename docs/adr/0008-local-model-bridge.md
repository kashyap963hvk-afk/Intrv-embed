# ADR 0008: Local Model Bridge and server-run GGUF models

- Status: accepted
- Date: 2026-10-20
- Context: Phase 9. ARCHITECTURE §10.

## Decisions

1. **The PC dials out; it never listens.** `intrv-bridge` keeps one WebSocket to `/api/bridge/connect`,
   authenticated by a device token. Tokens come from pairing: a signed-in member creates a 6-digit code
   (single use, 10 minutes, Valkey), the bridge exchanges it at `POST /api/bridge/claim`. Only the token's SHA-256
   is stored (`bridge_devices`, RLS private to the member); the tunnel looks it up through the SECURITY DEFINER
   function `app_bridge_device_by_token()` (ids only, revoked devices excluded).
2. **Any process can use the device.** The API replica that holds the socket relays through Valkey: requests are
   published to `bridge:dev:{device}`, responses to `bridge:res:{request}`, presence is a 30 s key refreshed every
   10 s. API replicas and Celery workers (grading) therefore all reach the same PC, and "offline" is known
   before a request is sent.
3. **A narrow protocol.** Server → bridge: `chat`, `models`, `cancel`, `ping`. The bridge validates every request
   (allow-listed ops, strict chat fields, no file or shell operation exists) and answers `status` / `chunk` /
   `done` / `err`. `status` frames keep the gateway's first-token timer alive while a model loads from USB.
   Closing the gateway stream publishes `cancel`, which aborts the local HTTP request.
4. **One request per model at a time**, others queue in order (`status: queued`); llama-server instances bind to
   127.0.0.1 on free ports, use `--no-mmap` for files on removable drives, and are unloaded when idle.
5. **Data protection.** BRIDGE and GGUF_MANAGED profiles are classified as not training on data: candidate data
   stays on the member's PC or the deployment. Bridge profiles are personal (they depend on one person's PC).
6. **GGUF_MANAGED (self-host only).** Owners point a profile at an absolute `.gguf` path. It must resolve, with
   symlinks followed, inside `GGUF_ALLOWED_DIRS`, have the GGUF magic bytes, and the server must be in self-host
   mode. The backend runs `LLAMA_SERVER_BIN` per model, health-checks it and unloads it after
   `GGUF_IDLE_UNLOAD_S`.
7. **Revocation is immediate.** Revoking publishes a close to the device channel; the holder closes the socket
   with 4403 and the bridge stops reconnecting until it is paired again.

## Consequences

- The llama.cpp binary the bridge downloads is verified against the SHA-256 digest GitHub publishes for the
  release asset; assets without a digest are refused.
- The tray UI is an optional extra (`pystray`, LGPL) so the default bridge build has no copyleft dependency.

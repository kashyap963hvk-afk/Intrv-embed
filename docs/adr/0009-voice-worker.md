# ADR 0009: Voice interviews through a separate voice worker

- Status: accepted
- Date: 2026-10-20
- Context: Phase 7. ARCHITECTURE §14. Pipecat 1.13 was verified in a scratch environment before use.

## Decisions

1. **The voice worker is an audio adapter, not a second engine.**
   - `voice/` has its own image and runs in the compose profile `voice`. It terminates WebRTC (Pipecat
     SmallWebRTC).
   - It opens the **same** `/ws/interview/{id}` socket as the text room, authenticated with the candidate's
     invite token.
   - The interview engine keeps every decision, all persistence, grading and integrity.
   - Text and voice share one session; "Switch to text" is a `mode` message.
2. **Why a separate process.** None of the following may enter the backend (CLAUDE.md rule 10):
   - aiortc depends on PyAV, whose wheels bundle FFmpeg with GPL codecs (libx264/libx265);
   - Kokoro's `kokoro-onnx` pulls in `phonemizer` and espeak-ng (GPL-3.0);
   - Pipecat's Piper module imports GPL `piper-tts`.

   The voice worker holds aiortc and faster-whisper (MIT). Kokoro runs in its own `tts/` container behind a small
   HTTP API, so the worker never imports GPL text-to-speech code.
3. **Pipeline:**
   - Order: transport in → Silero VAD (0.2 s) → speech-to-text → user turns (VAD start with barge-in, Smart Turn v3
     stop) → InterviewBridge → text-to-speech → transport out → SpokenTracker.
   - Text-to-speech uses token aggregation, because the engine already emits whole sentences.
   - It runs on `PipelineWorker`/`WorkerRunner` (the 1.13 API), with RTVI on for `@pipecat-ai/client-js`.
4. **What was heard is recorded.**
   - A `TTSTextFrame` reaches the processor after the output transport only once its sentence has played.
   - On barge-in, the rest of the reply is muted and `{"type": "spoken", seq, text, interrupted}` goes to the engine.
   - The engine stores `spoken_text` and `interrupted` on the interviewer turn.
5. **Candidates who pause are not cut off.**
   - Answers are sent 0.6 s after the end of a turn; the send is cancelled if the candidate speaks again.
   - If they keep talking over a reply they never heard, the next answer carries `continuation: true`.
   - The engine then restores the state saved before that exchange (`state_before`, voice sessions only), deletes
     the unheard reply and its answer, and treats both parts as one answer.
6. **Silence and rambling.**
   - 7 s after a question: "Take your time."
   - 20 s: a `nudge` asks the engine to rephrase the question more narrowly. This is stored as an interviewer turn
     and never graded.
   - 45 s: an offer to move on.
   - The timers are armed once per question, after the question has played. They are cancelled by the
     candidate's speech, not by the interviewer's prompts.
   - A monologue longer than 120 s gets a soft cut-in at the next pause.
7. **Speech-to-text options.**
   - faster-whisper in-process, with one model shared per worker, baked into the image (`HF_HUB_OFFLINE=1`).
   - Groq, or any OpenAI-compatible Whisper endpoint. These Pipecat services create an `openai.AsyncOpenAI` client
     for transcription only; LLM calls still go only through the gateway (rule 5).
8. **NAT traversal.**
   - The API gives the browser STUN plus short-lived TURN credentials via `/api/join/{token}/voice`
     (coturn REST scheme, `TURN_SHARED_SECRET`).
   - The voice worker and coturn use host networking for UDP media.

## Measured

Offline harness on CPU: real Silero VAD, Smart Turn v3 and Kokoro; scripted STT and engine (`make voice-bench`).

- **End of turn:** about 40 ms (p50) after speech stops, up to about 2 s when Smart Turn is unsure.
- **First sentence to first audio out:** about 330 ms with Kokoro on CPU. The cold start is 3.6 s, so the TTS
  container warms up when it starts.
- **Barge-in:** VAD start fires about 0.38 s after speech onset (`start_secs=0.2` dominates); the reply is muted
  in the same tick.
- **Not measured:** real STT latency. Whisper models cannot be downloaded in the build sandbox.

## Not covered yet (BACKLOG)

Audio integrity signals that need speaker models (second voice, whispering), and keyboard-transient detection.

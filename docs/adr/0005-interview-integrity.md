# ADR 0005: Interview integrity (anti-cheating) monitoring

- Status: accepted
- Date: 2026-10-18
- Supersedes: the "no gaze analysis" line in ARCHITECTURE §14.3 (ADR 0001 baseline)

## Context

The product goal now includes stopping candidates from cheating during AI-run interviews: reading answers
from another screen or tab, pasting LLM output, a helper in the room or on an earpiece, a different person
taking the interview, and AI "copilot" overlays. The owner asked for the AI to watch and listen, including the
candidate's eyes.

Facts that constrain the design:

1. **The EU AI Act bans emotion recognition in the workplace, including recruitment** (Art. 5(1)(f), in force
   since Feb 2025), and bans biometric categorisation that infers sensitive traits (Art. 5(1)(g)). Recruitment
   AI is high-risk (Annex III §4): it needs human oversight, logging, accuracy and transparency.
2. **Gaze or head-pose estimation is not emotion recognition**, and it does not identify anyone, so it is not
   banned. It still processes face images, so it needs a clear notice, consent, a stated purpose, and
   minimisation (GDPR Art. 5, 6, 13; India DPDP Act §6). **Illinois BIPA** treats a "scan of face geometry"
   as a biometric identifier. Facial landmarks probably qualify, so Illinois candidates need written consent
   and a published retention policy.
3. **Webcam gaze estimation is noisy.** It confuses people who look away to think, which is most people. It
   fails or is biased on glasses, low light, some skin tones under poor lighting, strabismus or nystagmus,
   and blind or low-vision candidates. HireVue dropped facial analysis in 2021 after an FTC complaint.
   Proctoring vendors (ProctorU, Respondus, Examity) have faced BIPA class actions.
4. **Determined cheaters beat eye tracking.** Overlay copilots (Cluely, Interview Coder and similar) put
   answers in an invisible window right next to the camera. They are hidden from screen capture by design,
   so the eyes never leave the screen. A phone propped behind the laptop, or an earpiece, defeats it too.
5. **The strongest defence is the interview itself.** A human-like interviewer that asks adaptive, personal,
   follow-up questions breaks the cheat loop: "You said you fixed the DMA race on the motor controller. What
   was in the ISR, and why didn't a volatile flag fix it?" So do practical rounds where the candidate explains
   their own code, with a human final round on top.

## Decision

Build an **Integrity Monitor** that collects **behavioural signals**, has the agentic interviewer respond to
them like a careful human would, and gives the human reviewer a **timeline of evidence**. It **never decides**
that someone cheated and never feeds the score.

### Signals (each is an `integrity_events` row: kind, severity, started_at, duration_ms, detail)

| Layer | Signal | Where computed | Phase |
|---|---|---|---|
| Browser | tab hidden / window blur (count, duration), fullscreen exit, second monitor (`screen.isExtended`), copy and paste (size, source field, timing), typed text arriving in bursts (paste-like cadence), right-click or devtools key combos, browser resize to a split screen | browser → `POST /api/join/{token}/integrity` or the interview socket (batched; the invite token is the authority, ADR 0007) | 6 |
| Screen | full-screen share required in STRICT: `displaySurface === "monitor"` verified, share stopped events; periodic low-res frames kept for the human reviewer only | browser; frames to S3 under the session prefix | 8 |
| Answers | answer latency outliers, long silence then a fluent textbook answer, near-verbatim overlap with model reference answers or retrieved KB text, style shift between answers | backend (deterministic and grader agent) | 6 |
| Prompt attacks | instructions aimed at the AI ("ignore previous…") | controller (already specified) | 6 |
| Audio | a second voice (speaker-change against the candidate's own first answers; embeddings kept in memory only and discarded at session end), whispering or background speech while the candidate is silent, keyboard typing while answering by voice, virtual audio devices | voice worker + browser device labels | 7 |
| Camera | face count 0 (left the seat) or 2+ (someone else in view); **head pose / gaze away from the screen** for more than N seconds (direction plus duration only); virtual camera devices (OBS, ManyCam, etc.); periodic snapshots for the human reviewer | **in the browser** with MediaPipe Tasks Vision (Apache-2.0); frames are never uploaded for analysis | 8 |

### How the agent reacts ("AI can think and hear")

- The controller receives an `integrity_context`: unaddressed signals since its last turn, summarised and
  without raw media.
- `policy.py` decides deterministically:
  - **Neutral reminder** (org-editable text, at most one per section): "Please keep this window in focus and
    full screen."
  - **Integrity probe**: the next question becomes a personalised deep-dive on the candidate's own claim or
    answer, drawn from the CV, transcript and role pack `probe` templates. It is hard to answer with outside
    help in real time.
  - **Pause**: only if the share or camera required by STRICT stops. The room waits until it resumes.
- The interviewer **never accuses**, never mentions cheating, and never changes its tone. The L0 prompt
  forbids it.
- Integrity signals are **excluded from grading**. Graders run blind to them. The report shows them in a
  separate panel: timeline, counts, durations, snapshot strip, and a rollup of "worth a look / nothing
  notable". It carries the label "Signals, not proof. A human must review before any decision."

### Modes (org policy, per template override)

- `OFF`: answer-level signals only.
- `STANDARD` (default): browser + answers + prompt attacks (+ audio in voice mode).
- `STRICT`: STANDARD + required full-screen share + camera presence and gaze + snapshots.
  - The candidate sees exactly what is monitored on the consent screen before starting.
  - Consent text is versioned and stored.
  - If the owner marks camera monitoring *optional*, declining continues in STANDARD and the reviewer sees
    "declined camera monitoring".
  - If *required*, the consent screen offers the accommodation contact instead of starting.
  - Camera monitoring can be disabled for specific jurisdictions in org policy (default list empty; the owner
    decides with their counsel).

### Permanently out of scope (CLAUDE.md rule 8, enforced by tests)

- Emotion, expression, stress, "confidence", nervousness, deception or lie detection, and personality from
  face or voice.
- Facial blendshapes: the face landmarker is created with `outputFaceBlendshapes: false`, and a unit test
  asserts it.
- Face recognition / identity matching.
- Age, gender or ethnicity inference.

Identity is checked by the human reviewer, who compares the start snapshot with the candidate's ID. An
optional third-party KYC integration is in BACKLOG.

### Data minimisation

- Gaze and face computation happens on the candidate's device.
- Only derived events (direction, duration, face count) leave the device.
- Snapshots are low resolution, taken every 60 s plus on events, and live under the session's S3 prefix.
- Snapshots are deleted with the session (retention policy and erasure cascade). Speaker embeddings are never
  persisted.

## Consequences

- Real deterrence and real evidence for reviewers. The candidate knows they are observed, which deters most
  casual cheating.
- False positives are expected and are handled by human review, never automation. Accommodations exist.
- We are honest in the UI and docs that overlay copilots and off-camera helpers cannot be reliably detected.
  Adaptive probing and practical rounds are the main defence.
- Frontend adds `@mediapipe/tasks-vision` (Apache-2.0). The model file is self-hosted under `/models` so
  offline and on-prem deployments work. The backend bans face and emotion libraries.

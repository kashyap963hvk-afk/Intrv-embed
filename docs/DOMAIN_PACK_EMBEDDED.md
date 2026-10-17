# Role Packs and the default Embedded Systems domain pack

A **Role Pack** is versioned data (YAML in `seed/role_packs/`, rows in `role_packs`) that tells every agent *what good looks like* for a role. Users can edit, fork and create role packs (or let the Role Builder agent draft one), so the same engine interviews a firmware engineer today and a data engineer, product manager or nurse tomorrow. **The engine does not hardcode any domain knowledge.**

## 1. Role Pack schema (validated by `backend/app/rolepacks/schema.py`)

```yaml
slug: string                      # unique per org + version
version: semver
name: string
description: string
domain_tags: [string]             # used to auto-suggest a pack from CandidateProfile.domain_tags
seniority_levels: [JUNIOR, MID, SENIOR, STAFF]
competencies:                     # the rubric backbone
  - id: string                    # e.g. emb.c_language
    name: string
    description: string
    anchors:                      # behaviorally anchored rating scale (BARS), 1..4
      1: "observable behavior at level 1"
      2: "..."
      3: "..."
      4: "..."
    signals: [string]             # concepts a strong answer touches
    red_flags: [string]           # misconceptions / bluff markers
    probe_ladder: [string]        # what → how → why → failure → debug, generic probes
    seed_questions:               # bank; the planner prefers CV-grounded variants
      - {text: string, difficulty: 1-5, level: [MID, SENIOR], expected_signals: [string], follow_ups: [string]}
roles:                            # variants that weight competencies differently
  - id: string
    name: string
    weights: {competency_id: float}   # sum to 1.0
    default_structure:                # overrides template sections
      - {section: OPENING|CV_WALKTHROUGH|TECHNICAL|PRACTICAL|BEHAVIORAL|CANDIDATE_QUESTIONS|CLOSE, minutes: int}
practical_tasks:                  # coding/debugging/whiteboard
  - id: string
    kind: DEBUG_SNIPPET|WRITE_CODE|WHITEBOARD|DATASHEET_READING
    language: c|cpp|python|rust|none
    prompt: string
    starter_code: string
    known_bugs: [string]          # for DEBUG_SNIPPET; Code Reviewer compares
    tests: [{stdin: string, expected_stdout: string}]   # for WRITE_CODE via Piston
    rubric_competencies: [string]
behavioral:
  - {competency_id: string, question: string, star_signals: [string]}
persona_defaults:
  interviewer_name: string
  style: string                   # appended to L2 prompt
kb_hints:                         # what owner should upload for best grounding
  - string
```

The schema is validated on save. Weights must sum to 1.0 (±0.01), every `rubric_competencies` entry must reference an existing competency, and anchors 1–4 are required.

## 2. The Embedded Systems pack (default)

The full file is `seed/role_packs/embedded-systems.yaml`. Overview:

| Competency | What we test | Why it separates real engineers from CV padding |
|---|---|---|
| `emb.c_language` | Pointers, `volatile`, `const`, integer promotion, alignment and packing, UB, bit manipulation, `static` scope, inline asm awareness | Most embedded bugs are C semantics bugs. Bluffers know the words, not the consequences. |
| `emb.mcu_architecture` | Cortex-M core and NVIC, memory map, clocks and PLL, startup code and vector table, linker script sections (.data/.bss copy), MPU, caches on M7 | "I used STM32" ≠ understanding what happens before `main()`. |
| `emb.interrupts_concurrency` | ISR design, latency, priorities and preemption, shared data, critical sections, atomics, re-entrancy, priority inversion | The #1 source of field failures. |
| `emb.rtos` | FreeRTOS/Zephyr tasks, scheduling, queues, semaphores vs mutexes, stack sizing, tickless idle, deadlocks, watchdog strategy | Using an RTOS API vs designing with one. |
| `emb.peripherals_protocols` | GPIO, timers/PWM, ADC/DAC, DMA, UART, SPI, I2C (clock stretching, bus lockup recovery), CAN/CAN-FD (arbitration, error states), USB basics, BLE basics | Protocol depth shows hands-on scope time. |
| `emb.low_power` | Sleep modes, wake sources, clock gating, current profiling, battery budgeting | Critical for IoT/wearables. |
| `emb.debugging_tools` | JTAG/SWD, GDB, HardFault analysis (CFSR, stacked PC), logic analyzer, oscilloscope, trace (ITM/SWO), printf cost | Real debugging stories are hard to fake. |
| `emb.embedded_linux` | Boot flow (ROM→SPL→U-Boot→kernel→rootfs), device tree, kernel modules and drivers, Yocto/Buildroot, cross-compilation, sysfs/ioctl | For BSP/driver roles. |
| `emb.hardware_literacy` | Reading schematics and datasheets, pull-ups, level shifting, decoupling, ADC reference/noise, signal integrity basics | Firmware engineers who can't read a schematic stall teams. |
| `emb.reliability_safety` | MISRA C, defensive coding, watchdogs, brown-out, memory protection, ISO 26262 / IEC 61508 awareness, fault injection | Automotive, medical, industrial. |
| `emb.security_ota` | Secure boot, chain of trust, firmware signing, OTA with A/B partitions and rollback, debug-port lockdown, TLS on constrained devices | Increasingly mandatory (EU CRA). |
| `emb.testing` | Unit testing on host (Unity/Ceedling, GoogleTest), mocking HAL, HIL rigs, CI for firmware, static analysis | Separates mature engineers. |
| `emb.system_design` | Architecting a device end-to-end: partitioning, buffers, data flow, timing budgets, failure modes | Senior/staff signal. |
| `beh.ownership`, `beh.collaboration`, `beh.learning` | Behavioral (STAR) | Same everywhere. |

Role variants (weights in YAML): **Firmware Engineer (MCU)**, **Embedded Linux / BSP Engineer**, **Device Driver Engineer**, **Automotive Embedded Engineer**, **IoT / Connectivity Engineer** and **Embedded Test / HIL Engineer**.

## 3. How users adapt it to *any* role

1. **Fork and edit**: in `/app/role-packs`, *Fork* the Embedded pack, change weights, add or remove competencies, and write your own anchors and questions (form editor or raw YAML with live validation).
2. **Role Builder agent**: describe the role in plain words, optionally pick KB spaces (for example "our internal coding standards"). The agent drafts a full pack grounded in those docs. You review the diff and save.
3. **Per-interview tweaks**: in the template, override section minutes, follow-up depth, strictness, language and persona prompt (L2) without touching the pack.
4. **Owner publishing**: owners can publish packs org-wide (`visibility=ORG`). Members can fork them but not edit the original.

## 4. What the Owner should upload to the KB for elite grounding (Embedded)

- MCU reference manuals and datasheets the team uses (STM32 RM/DS, nRF52 PS, ESP32 TRM), errata sheets
- Internal coding standard (MISRA deviations, naming), HAL/driver architecture docs
- RTOS configuration guidelines, past incident postmortems (anonymized), which make excellent "what broke in production" probes
- Interview rubrics and calibrated example answers from senior engineers
- Board schematics (PDF), which feed DATASHEET_READING / whiteboard tasks via the vision agent

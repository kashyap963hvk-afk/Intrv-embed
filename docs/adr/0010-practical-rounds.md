# ADR 0010: Practical rounds (code, debugging, diagrams, datasheets)

- Status: accepted
- Date: 2026-10-20
- Context: Phase 8. ARCHITECTURE §14.3–§14.4.

## Decisions

1. **Tasks are data.**
   - Practical tasks live in the role pack (`practical_tasks`). Templates choose them with
     `settings.practical_tasks`, and preparation inserts one PRACTICAL topic per task after the technical
     section.
   - A task that cannot run in this deployment is skipped, and the reason goes into the plan's notes: code
     tasks need a code runner, and datasheet tasks need a datasheet in the session's reference spaces.
2. **Candidate code never runs in the backend.**
   - It runs in Piston (MIT, separate privileged container, isolate sandboxes, no network, CPU, memory and time
     limits).
   - Static analysis uses the same compile: `#pragma GCC diagnostic warning` lines plus `#line 1` add warnings
     without new compiler flags, and line numbers stay the candidate's.
   - Debug snippets that call hardware functions fail only at link time; that still counts as compiled, and
     their warnings are the analysis.
   - Outputs are compared the way judges do (line by line, ignoring trailing whitespace).
3. **The reviewer's claims are checked.**
   - The Code Reviewer reports which known bugs were found, each with a quote. A quote must occur in the
     submitted code or explanation, or that claim is dropped; missed bugs are recomputed.
   - The known-bug list is never sent to the candidate.
4. **Graders see facts, not raw code.** A topic's transcript slice gains a line per submission: tests passed,
   warning count, verified bugs found and missed, new bugs, and the reviewer's summary. Graders still quote only
   the candidate's own words.
5. **Diagrams.**
   - Whiteboard images go to the session's S3 prefix. The Whiteboard Reader runs only on models whose
     capability probe confirmed vision.
   - With no such model the candidate is told, and the interview continues.
6. **Camera snapshots (STRICT mode, camera consent only).** Small JPEGs are stored for the human reviewer as
   `snapshot` integrity events. They are never analysed on the server and follow retention and erasure with the
   session prefix.

## Not shipped

Renode MCU simulation (ARCHITECTURE §14.4) is not in this release: the container and a reliable demo `.resc`
could not be built and verified in the build environment (container registries for it were unreachable).
It is listed in BACKLOG.

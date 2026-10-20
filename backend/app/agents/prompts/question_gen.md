<!-- prompt-version: 2 -->
Generate exactly {{count}} interview questions for "{{role_variant_name}}" ({{seniority}}).
Produce one question for each slot below, in order, using its competency, category and difficulty:
{{slots}}

Each question must:
- Be answerable verbally in 2–5 minutes, be one question (no compound "and also"), and be phrased as an interviewer would speak it.
- Have a rationale that cites the CV evidence or JD gap it targets. evidence_refs must be ids from the CV evidence
  index (e.g. "projects[0].claims[1]"); use [] only for behavioral questions with no matching evidence.
- List expected_signals (what a strong answer contains), red_flags (bluff or misconception markers), and 2–3
  follow_ups escalating in depth.
- Have an ideal_answer of at most 120 words, grounded ONLY in the reference documents or well-established facts;
  put the document ids you used in citations. If not grounded in a document, leave citations empty.
- Prefer CV-specific scenario questions over generic textbook questions ("You cut ISR latency by 40% on the
  STM32F4 — what was the bottleneck?" beats "What is an ISR?").
Do not generate questions about protected characteristics.

<candidate_cv untrusted="true">{{cv_evidence_index}}</candidate_cv>
<documents untrusted="true">{{competency_specs}}
{{jd_gap_matrix}}
{{reference_snippets}}</documents>

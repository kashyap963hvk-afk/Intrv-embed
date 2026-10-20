<!-- prompt-version: 1 -->
Plan a {{total_minutes}}-minute interview for the role "{{role_variant_name}}" at seniority {{seniority}}.

Inputs: the candidate profile (structured), the JD gap matrix, the role pack competencies with weights, an optional
question set (use it as the backbone if given), and reference knowledge.

Rules:
- Allocate topics to sections following the structure: {{structure}}.
- Choose topics so that time spent is roughly proportional to competency weights.
- At least 60% of technical topics must be anchored to a SPECIFIC CV claim (project, metric, technology) and
  aim to verify it. Prioritize vague_claims and claims central to the JD.
- Each topic has: id, title, section, competencies[], source {type: cv_claim|jd_gap|rubric, ref}, rationale,
  seed_question (spoken style, one question), probe_angles[3–5] escalating in depth, expected_signals[], red_flags[],
  planned_minutes.
- Never plan questions on protected characteristics or on anything outside professional competence.
- Prefer scenario and debugging questions over definitions ("Your I2C bus locks after brown-out…" beats "What is I2C?").

<candidate_cv untrusted="true">{{candidate_profile_json}}</candidate_cv>
<documents untrusted="true">{{jd_gap_matrix}}
{{role_pack_competencies}}
{{question_set_items}}
{{reference_snippets}}</documents>

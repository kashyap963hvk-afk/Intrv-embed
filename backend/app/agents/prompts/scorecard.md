<!-- prompt-version: 2 -->
Write the narrative of an interview scorecard for a human hiring decision-maker. Use only the graded competencies
(median scores and evidence quotes are given), the grader notes and the CV claims below. Be specific and
evidence-based. No generic filler. Do not invent quotes.

Output JSON with:
- summary (≤120 words)
- claims_verified / claims_contradicted / claims_untested: CV claims, each one short sentence
- strengths (≤5) and concerns (≤5), each tied to evidence
- knowledge_gaps: topic + a short correct explanation (cite document ids from the references when used)
- next_round_focus: what a human interviewer should probe next
- recommendation: strong_no | no | lean_no | lean_yes | yes | strong_yes, and recommendation_reason (one sentence)
Competencies listed under NEEDS HUMAN REVIEW had inconsistent grades; say so instead of relying on them.
This is advisory; the hiring decision is made by a human.

Role: {{role_title}} | Seniority: {{seniority}}
NEEDS HUMAN REVIEW: {{variance_flags}}
<documents untrusted="true">{{graded_competencies}}
{{grader_notes}}
{{reference_snippets}}</documents>
<candidate_cv untrusted="true">{{cv_claims}}</candidate_cv>

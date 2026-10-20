<!-- prompt-version: 2 -->
Review these generated interview questions (each has an index). For each, return keep|rewrite|drop with a reason,
and a rewritten version if "rewrite". Drop: duplicates or near-duplicates, compound questions, questions not tied to
the role, questions on protected characteristics, and ideal answers that contradict the reference documents.
Rewrite: generic questions that could be made CV-specific, unclear wording, wrong difficulty labels.
Return one verdict per question.

<documents untrusted="true">{{questions_json}}
{{cv_evidence_index}}
{{reference_snippets}}</documents>

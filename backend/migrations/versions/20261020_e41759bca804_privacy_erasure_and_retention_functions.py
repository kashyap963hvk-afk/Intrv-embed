"""privacy erasure and retention functions

Revision ID: e41759bca804
Revises: f4d135e64518
Create Date: 2026-10-20 16:29:55.432788

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e41759bca804"
down_revision: str | Sequence[str] | None = "f4d135e64518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DEFINER = "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp"

# Every row that holds a candidate's personal data in the CALLER's organization (app.org_id), with the member who
# owns it, so an owner can find and erase a subject's data across members (ids and labels only).
CANDIDATE_INDEX = f"""
CREATE FUNCTION app_candidate_index()
RETURNS TABLE (kind text, id uuid, owner_user_id uuid, label text, document_id uuid, profile_id uuid)
{_DEFINER} AS $$
  SELECT 'session', s.id, s.owner_user_id, s.candidate_label, s.cv_document_id, s.candidate_profile_id
    FROM interview_sessions s WHERE s.org_id = app_current_org()
  UNION ALL
  SELECT 'profile', p.id, p.owner_user_id, '', p.document_id, p.id
    FROM candidate_profiles p WHERE p.org_id = app_current_org()
  UNION ALL
  SELECT 'cv', d.id, d.uploader_id, d.title, d.id, NULL
    FROM documents d WHERE d.org_id = app_current_org() AND d.doc_type = 'CV' AND d.uploader_id IS NOT NULL
  UNION ALL
  SELECT 'question_set', q.id, q.owner_user_id, q.title, q.cv_document_id, q.candidate_profile_id
    FROM question_sets q WHERE q.org_id = app_current_org()
$$
"""

# Nightly retention purge, across organizations: candidate data older than the org's retention_days.
EXPIRED = f"""
CREATE FUNCTION app_expired_candidate_data(max_rows integer)
RETURNS TABLE (kind text, id uuid, org_id uuid, owner_user_id uuid)
{_DEFINER} AS $$
  WITH orgs AS (
    SELECT o.id, make_interval(days => COALESCE((o.settings->'policy'->>'retention_days')::int, 180)) AS keep
    FROM organizations o
  )
  (SELECT 'session', s.id, s.org_id, s.owner_user_id FROM interview_sessions s JOIN orgs ON orgs.id = s.org_id
    WHERE s.status <> 'LIVE' AND COALESCE(s.ended_at, s.updated_at) < now() - orgs.keep LIMIT max_rows)
  UNION ALL
  (SELECT 'question_set', q.id, q.org_id, q.owner_user_id FROM question_sets q JOIN orgs ON orgs.id = q.org_id
    WHERE q.updated_at < now() - orgs.keep LIMIT max_rows)
  UNION ALL
  (SELECT 'cv', d.id, d.org_id, d.uploader_id FROM documents d JOIN orgs ON orgs.id = d.org_id
    WHERE d.doc_type = 'CV' AND d.uploader_id IS NOT NULL AND d.updated_at < now() - orgs.keep
      AND NOT EXISTS (SELECT 1 FROM interview_sessions s WHERE s.cv_document_id = d.id)
      AND NOT EXISTS (SELECT 1 FROM question_sets q WHERE q.cv_document_id = d.id)
    LIMIT max_rows)
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CANDIDATE_INDEX)
    op.execute(EXPIRED)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP FUNCTION IF EXISTS app_expired_candidate_data(integer)")
    op.execute("DROP FUNCTION IF EXISTS app_candidate_index()")

# ADR 0002: Identity, sessions and tenant isolation

- Status: accepted
- Date: 2026-10-17
- Context: Phase 1. ARCHITECTURE §4 (tenancy and access control).

## Decisions

1. **Tenant isolation is enforced by Postgres Row-Level Security**, not only by application code. Every
   tenant table has `ENABLE` + `FORCE ROW LEVEL SECURITY` and a policy `org_id = app_current_org()`, which reads the
   transaction-local setting `app.org_id`. The runtime role `app_rw` is `NOBYPASSRLS` and does not own the tables.
   Without a context, queries return nothing and writes fail (fail-closed).
2. **The context is applied at the start of every transaction** by a SQLAlchemy `after_begin` listener
   (`app/db/tenant.py`). A commit in the middle of a request therefore cannot drop it.
3. **Cross-tenant reads happen only through five SECURITY DEFINER functions** owned by `app_owner` (BYPASSRLS):
   `auth_users_by_email`, `auth_refresh_token`, `auth_invite`, `auth_org_by_slug`, `auth_org_count`. They return
   only the columns that authentication needs. These are the only routes across tenants, and a test pins their
   column lists.
4. **Email addresses are unique per organization**, not globally. Login looks the email up across organizations.
   If the password matches in several of them, the API answers 409 with the list (after the password is proven), and
   the client retries with `org_slug`. Unknown email and wrong password produce the same 401 with the same work done
   (dummy argon2 verify).
5. **Sessions**: a 15-minute HS256 access JWT in an httpOnly cookie, plus an opaque refresh token (stored as
   SHA-256) in an httpOnly cookie scoped to `/api/auth`, rotated on every use. Presenting an already-rotated token
   revokes the whole family (theft detection). A 15-second grace window treats two tabs refreshing at once as a
   benign race. Each request also checks that the token's family is still live, so logout, suspension and password
   changes take effect immediately, not after the access token expires.
6. **CSRF**: double-submit cookie plus an `X-CSRF-Token` header on every state-changing request, including
   login. `GET /api/auth/csrf` returns the token in the body as well, so a frontend on another subdomain can send it
   without reading cookies. Cookies are `SameSite=Lax`, `Secure` when the API is https.
7. **Membership is gated by owners**: self-signup creates `PENDING` users, who can sign in only to see their
   status. Owners approve or reject, and can invite (`MEMBER` or `OWNER`) with single-use, expiring, hashed tokens.
   The API always returns the invite link, so installs without SMTP still work. The last active owner cannot be
   suspended, demoted or deleted.
8. **First run**: with `OWNER_BOOTSTRAP_EMAIL` set and an empty database, the API creates the bootstrap org and
   logs and emails an owner invite. It is idempotent across replicas (unique slug). `make create-owner` (CLI) is the
   alternative.
9. **Audit log** rows are written in the same transaction as the change. `app_rw` has no UPDATE/DELETE on
   `audit_logs`.
10. **Rate limits** (Valkey fixed window): login 30/15 min per IP and 10/15 min per email; signup 10/h per IP;
    invite accept 20/h per IP; password change 10/h per user.

## Consequences

- Managed Postgres must allow `BYPASSRLS` for the migration role (docs/DEPLOYMENT.md §4.2).
- Background workers (Phase 3+) must set the tenant context from the job's `org_id` before touching tenant tables.
- Empty environment variables are treated as unset (`env_ignore_empty`). An empty `SMTP_USER` had made every email
  fail with an AUTH error; the browser walkthrough with Mailpit caught it.

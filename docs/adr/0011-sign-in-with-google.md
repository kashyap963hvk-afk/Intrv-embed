# ADR 0011: Sign in with Google, invite-gated, with password sign-in for owners only

- Status: accepted
- Date: 2026-10-21
- Context: Owners give access to any Gmail address. Members should sign in with Google, and only owners
  with a password. ARCHITECTURE §3 had planned optional OIDC SSO through Authlib.

## Decisions

1. **The OIDC authorization-code flow with PKCE, a nonce and a single-use state** (Valkey, 10 minutes),
   built directly on httpx in `app/auth/google.py`, without Authlib.
   - The ID token comes straight from Google's token endpoint over TLS. Its claims are checked: issuer,
     audience, expiry, nonce, and `email_verified`. The signature is not checked, as OIDC Core 3.1.3.7
     allows for a token received this way, so no JWKS fetching or caching is needed.
   - Scopes are `openid email profile` only, which need no Google app verification.
2. **Google proves identity; invitations grant access.**
   - The first sign-in must come through an owner's invitation link. The invite token travels in the state,
     and the Google email must equal the invited email.
   - Later sign-ins find the account by its verified email.
   - The Google `sub` is pinned on first use, so a different Google account with the same address cannot
     take an account over.
   - Uninvited, pending and suspended accounts are refused.
   - Anything written during a refused sign-in is rolled back.
3. **The org policy `member_sign_in: password | google`.** Default `password`. With `google`, password
   sign-in and password invite acceptance are refused for everyone except owners. The invitation preview
   tells the page which flow to show.
4. **Redirects.**
   - After sign-in, the browser returns to `PUBLIC_APP_URL` plus a same-site path only; absolute URLs, `//`
     and backslashes are dropped, which prevents an open redirect.
   - Errors return to `/login?error=…`.
   - Session cookies are the same as for password sign-in (§3).

## Consequences

- Google sign-in needs `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`, plus the redirect URI
  `https://<api>/api/auth/oidc/google/callback` registered in Google Cloud. The same client can also serve
  the Drive connector.
- Microsoft sign-in can follow the same pattern (BACKLOG).

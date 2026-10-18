# ADR 0006: Storage connectors and sync

- Status: accepted
- Date: 2026-10-18
- Context: Phase 4. ARCHITECTURE §6.

## Decisions

1. **Accounts and sources are separate rows.**
   - `connector_accounts` holds credentials, encrypted with the org's data key (AAD bound to the account id).
     It is private to the member who connected it; `owner_user_id = NULL` marks org-level storage that only
     owners may see or use.
   - `connector_sources` is a selection imported into one space, run as its creator. If the creator loses
     write access to the space, the sync fails instead of writing.
   - Members never see org-level accounts. Otherwise a member could pull org storage into a private space
     that owners cannot read.
2. **One contract, two change models.**
   - Snapshot-diff (S3, Azure, Google Drive, URL): list the selection and compare against the imported
     documents' etags. Simple, correct after any outage, and it costs one listing per sync.
   - Delta cursors (Microsoft Graph `root/delta`, Dropbox `list_folder/continue`): incremental. An expired
     cursor (410 / `reset`) falls back to a full listing.
   - Graph delta runs on the drive root, filtered by the selected folders' paths, because Graph does not
     support delta on non-root folders for OneDrive for Business and SharePoint.
   - The first Graph sync lists the folders and starts from `token=latest`, so it never walks the whole drive.
3. **Confinement.**
   - S3 and Azure accounts have a root prefix. Browse paths are normalised and refused if they leave it, and
     every listed key is re-checked.
   - Org Google Drive is limited to folders registered as roots and shared with the service account; folder
     ancestry is checked on browse and sync.
   - User Google Drive uses the `drive.file` scope with the Picker, so the app only sees what the user picked.
     The scope is not restricted, so no CASA assessment is needed.
4. **SSRF.**
   - User-typed endpoints (S3-compatible, Azure) pass the static URL checks and are resolved to public
     addresses before **every** use, because SDK clients open their own sockets.
   - Provider APIs use a fixed host allow-list, and pagination links pointing elsewhere are refused.
   - URL sources use the connect-time-guarded client and respect robots.txt.
5. **OAuth.**
   - Authorization code with PKCE (S256).
   - State is single-use, kept in Valkey for 10 minutes, and bound to the user who started the flow.
   - The callback checks it against the logged-in user, stores tokens only encrypted, and redirects the popup
     to `/app/knowledge/connected`.
   - Tokens are refreshed 60 s before expiry and re-encrypted. A 401 triggers one forced refresh; a second
     401 marks the account as needing reconnection.
6. **Versioning and deletion.**
   - Content is compared by SHA-256 (an etag change with the same bytes only updates the etag).
   - Changed content becomes a new `version` with a new object key. Ingestion writes the new vectors before
     deleting the old ones, and removes older originals once the document is READY.
   - Remote deletions, and files leaving the selection, delete the document, its vectors and its files (after
     commit) and are audited.
   - Work is committed per file, so a crash loses at most one file. At most 500 files are processed per run;
     a truncated run keeps the old cursor so the rest is picked up next time.
7. **Scheduling.**
   - Celery beat runs `sync.due` every minute. It reads due source ids through the SECURITY DEFINER
     `app_due_connector_sources()` (ids only), then each `sync.source` task runs inside its creator's tenant
     context.
   - A Valkey lock prevents concurrent syncs of one source.
   - Failures back off exponentially from the source's interval (capped at 24 h). The account is flagged on
     authentication errors, and the creator is emailed on the first failure.

## Consequences

- Google Drive (user and org), OneDrive/SharePoint and Dropbox are verified with recorded HTTP contract tests
  (respx). Live tests need real app credentials (see DEPLOYMENT.md §4.3).
- S3 is verified end to end against SeaweedFS (import, modify, delete, confinement, worker and beat path).
  Azure Blob is verified the same way against Azurite.
- Unexpected errors never leave a source stuck in SYNCING. One bad file fails alone; the rest of the run
  continues. The due scan also re-picks runs left in SYNCING for over 2 hours (a crashed worker).

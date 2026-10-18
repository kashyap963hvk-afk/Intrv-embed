# ADR 0004: Knowledge spaces, ingestion and hybrid retrieval

- Status: accepted
- Date: 2026-10-18
- Context: Phase 3. ARCHITECTURE §4.2, §7, §8.

## Decisions

1. **One permission function.** `app_space_permissions()` (SECURITY DEFINER) is the only definition of
   who can read or contribute to a space. It covers personal ownership, the owner role on org spaces, and
   direct and group grants with expiry. RLS on spaces, documents and chunks calls it through
   `app_readable_space_ids()` / `app_writable_space_ids()`. The API uses the same function, cached in Valkey per
   user under a per-org version number.
   - The version is bumped **after commit** on any grant, group, role, space or member change, so revocation
     takes effect on the very next request.
   - The SSE stream re-reads permissions as soon as the version changes.
   - The `space_read` policy also lists the space's manager explicitly. This is needed because `INSERT …
     RETURNING` checks the new row against the read policy, and the function's snapshot cannot see that row.
2. **Defence in depth for vectors.** Every Qdrant point carries `org_id` (tenant index) and `space_id`, and the
   query filter is built server-side from readable spaces. Hits are then **re-loaded through RLS**, keeping only
   READY documents in the requested spaces. A vector that outlives its row, or a filter bug, can never surface
   text.
3. **Hybrid search in one Qdrant query.**
   - Dense (bge-m3 via TEI for quality; fastembed bge-small for the CPU-only default; any OpenAI-compatible
     `/embeddings`) and our own BM25 sparse vectors (Qdrant `Modifier.IDF`).
   - A third, identifier-only sparse prefetch so register names like `GPIOx_BSRR` match exactly.
   - Fusion is RRF, then an optional cross-encoder reranker (TEI or fastembed) with a token budget.
   - We wrote the BM25 encoder (snowball stemming, identifiers kept verbatim) instead of using fastembed's,
     because model downloads are optional and embedded identifiers need custom tokenisation.
4. **Parent/child chunks.** Children of ~300 tokens with 50-token overlap are embedded; parents of ~1200
   tokens are what the LLM reads.
   - Headings set section paths, and tables and code blocks are never split.
   - Datasheet tables get their own chunks, and CVs get one parent per section.
   - Oversized single paragraphs are split to the parent budget.
   - Each child is embedded with "title > section path" prepended (cheap contextual retrieval).
5. **Parsing without AGPL.**
   - pdfplumber, python-docx, python-pptx, openpyxl and BeautifulSoup do the parsing. RapidOCR (Apache-2.0)
     handles scanned pages and images.
   - Docling is used automatically when installed.
   - RapidOCR runs on `opencv-python-headless` (a uv override replaces the GUI build), so images need no
     system graphics libraries.
   - **Hidden text** (white, tiny or off-page characters) is excluded from what the AI sees and is recorded as
     a risk flag, together with prompt-injection patterns, for human reviewers.
6. **Uploads.**
   - Content-sniffed MIME types, with binaries rejected even when they decode as UTF-8.
   - Zip archives are unpacked with member-count, total-size and ratio limits; nested archives are refused.
   - SHA-256 de-duplication per space.
   - URL documents go through the SSRF guard.
   - Files live in S3 under `org/{org}/space/{space}/doc/{doc}/v{n}/`. Downloads are 5-minute SigV4
     presigned links, signed for `S3_PUBLIC_ENDPOINT_URL` when browsers reach storage on a different host.
7. **Side effects after commit.** Enqueueing ingestion, bumping the ACL version, and deleting vectors and files
   all run as after-commit hooks, so a rolled-back request leaves nothing behind and workers never see
   uncommitted rows.
   - Ingestion runs in Celery (`ingest` queue) with one event loop and one set of clients per worker
     process. `INGEST_INLINE=true` runs it in-process for single-process demos and tests.
8. **Embedding model changes create a new collection** (`kb__{model}__{dim}`), never a mixed one; documents
   are reindexed into it.

## Consequences

- With the lexical test embedder, the golden set (49 queries over a datasheet PDF, RTOS notes DOCX and a bus
  protocols note) scores recall@6 = 1.0 with zero leakage. Real models are expected to do at least as well on
  paraphrased queries. Re-run with `EMBED_BACKEND=tei` when the models are available.
- Members' personal spaces are invisible to owners, both in the database and in live events. Deleting a member
  purges their private vectors and files after the commit.

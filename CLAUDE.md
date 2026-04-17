# ContactRefiner — Dev Notes

## Local Dev
- Dashboard: `cd dashboard && GOOGLE_APPLICATION_CREDENTIALS=/tmp/dashboard-reader-key.json pnpm dev`
- SA key may expire — restore with gcloud (see ops docs, not committed)
- Python pipeline: `uv run python main.py analyze` / `uv run python main.py fix --auto`
- Full Python CLI: `backup`, `analyze`, `fix`, `fix --auto`, `ai-review`, `followup`, `ltns`, `tag-activity`, `crm-sync`
- Dashboard build: `cd dashboard && pnpm build` / `pnpm preview` (test prod locally)
- **Dev server cleanup**: Global PreToolUse hook auto-kills stale Nuxt processes before starting new `pnpm dev`

## Deploy
- Render auto-deploys dashboard on push to main (~5min)
- Cloud Build auto-deploys Cloud Run Job on push to main (~3min); trigger: `deploy-on-push` in europe-west1 (GitHub App on peter-fusek account); uses `--update-env-vars` (NOT `--set-env-vars` which wipes all vars); must include `ENVIRONMENT=cloud`
- Cloud Build trigger branch pattern must be exactly `^main$` — double-regex (`^main$^main$`) silently breaks auto-deploy
- GA4 Measurement ID: `G-WRBBPFCSPS` (in nuxt.config.ts head scripts)

## GCP Auth
- gcloud auth opens Safari by default — must manually copy URL to Chrome (Chrome-only policy)
- GCS SA role: needs **Object Admin** (not Object Creator) for file overwrites
- Cloud Run Job name: `contacts-refiner` (NOT `contacts-refiner-job`)
- Cloud Build auto-deploys on push to main, takes ~3min — don't trigger Cloud Run Job immediately after push

## Code Conventions
- All system text in English — rules, reasons, logs, errors, git messages
- Contact data is Slovak (SK) — do not treat as Polish
- Error messages returned to client must be generic (no internal details)
- Corrupt decision files go to `data/failed/` (not retried)

## Key Architecture
- Pipeline phases: 0 (review feedback) → 1 (backup, analyze, fix HIGH) → 2 (AI review MEDIUM) → 3 (activity tagging) → 4 (FollowUp scoring) → 5 (CRM sync)
- Phase 4 gated by `ENABLE_FOLLOWUP_SCORING` env var (**enabled** in Cloud Run since 2026-03-23)
- Phase 5 gated by `ENABLE_CRM_SYNC` env var (**enabled** in Cloud Run since 2026-03-31) — syncs CRM notes → biographies, tags → contact groups (additive only)
- GCS is the message bus: workplan → analyze → queue → review → export → apply
- Review sessions in `data/review_sessions/`, decisions in `data/review_decisions_*.json`
- Review changeIds: hash `resourceName|field|newVal` only (NOT oldVal — it changes between re-analyses)
- Review session matching: `getSessionForReviewFile()` finds session by `reviewFilePath`, not just latest
- Review auto-save: 1s debounce + `sendBeacon` on `beforeunload` for reliable persistence
- Feedback learning in `data/feedback.jsonl`
- `readJson` returns null ONLY on 404 — throws for all other GCS errors (auth, permissions, etc.)
- Emergency stop: `data/pipeline_paused.json` (written by dashboard, checked by entrypoint.py)
- All exit paths in `run()` must go through `_finalize_run()` (record + email + log) — never call `_record_pipeline_run` + `return` directly
- Changelog dedup key: `resourceName|field|old|new` (no session_id) — changelog.get.ts skips dedup when filtering by sessionId to preserve per-run drill-down
- batch_processor: changelog logged AFTER API success (not before); no-op round-trips (same field, final new == original old) are suppressed
- Google Contacts account: peterfusek1980@gmail.com is u/1 in Chrome (not u/0) — always use u/1 for contact searches/creation

## Dashboard Patterns
- Name resolution: `getContactNameMap()` in gcs.ts — resolves resourceName → displayName from workplan + LinkedIn signals; changelog.get.ts also enriches inline from changelog `names[0].displayName` entries
- CRM state: `data/crm_state.json` in GCS — stage, notes, tags per contact; `getCRMState()`/`saveCRMState()` in gcs.ts
- CRM API: GET /api/crm (merged followup+signals+state), POST /api/crm/update, POST /api/crm/batch-move
- CRM-only contacts: contacts added via API without follow-up scores appear in CRM view if stage is non-inbox; `name` field in CRMContactState used for display
- resourceName format: Google People API uses `people/c123456` (with `c` prefix) — all regex validation must use `/^people\/c?\d+$/`
- Cache: 60s TTL in-memory Map; `clearCache()` exposed via POST /api/cache-clear
- Demo masking: `demo.ts` — must handle ALL PII fields including `field === 'contact'` (tobedeleted names)
- API sub-routes: use directory structure (e.g., `api/config/index.get.ts` + `api/config/pause.post.ts`)
- Nuxt API routes: ALL endpoints need `isDemoMode()` guard (repo is public, unauthenticated users get empty data) — exception: `/api/health` is intentionally unauthenticated (non-sensitive aggregate metrics only)
- Drag-and-drop: CRMColumn uses counter-based dragenter/dragleave (NOT relatedTarget — it's null in Chrome for drag events)
- Nav order: Status, Signals, Review, CRM, LinkedIn, Changelog, Runs, Pipeline, Config (Analytics/Social Signals/FollowUp removed from nav, pages still exist or redirect)
- /signals page (Sprint 3.30): derives 7 signal-type badges from followup_scores.json + LinkedIn; candidates/backlog/dismissed tabs; 100/week cap; accept→CRM inbox, dismiss with preset reason; derivation logic in `server/utils/lead-signals.ts`; state in `data/lead_signals_state.json` (GCS)
- Business-first scoring (#141): FollowUp scoring caps `months_gap` at 24mo, adds exec-title bonus (+15), penalises personal contacts (×0.3 when no org/title/LinkedIn + personal email only), excludes own-company (Instarea), drops 5yr silent unless valid LinkedIn job_change (≥15-char headline); constants in config.py `FOLLOWUP_*`
- LinkedIn CRM: `/linkedin-crm` page — local JSON data via Nitro serverAssets (`useStorage('assets:data')` in production, `readFile` fallback for dev); types in `server/utils/types.ts` (LI* prefix); seed data in `server/data/linkedin-crm.json`
- LinkedIn CRM data helper: `server/utils/linkedin-crm-data.ts` — `getLinkedInCRMData()` / `saveLinkedInCRMData()`
- LinkedIn CRM mutations: POST handler with action dispatch (`updateContactStatus`, `updateContactNotes`, `updateContactTier`, `logDM`, `addFollowerSnapshot`); optimistic updates on client, capture contact ref before await
- LinkedIn CRM storage: Nitro serverAssets (in-memory in production) — writes lost on redeploy; migration to GCS/SQL planned
- Nitro serverAssets: configured in `nuxt.config.ts` → `nitro.serverAssets` for bundling `server/data/` into production build
- Security headers: X-Frame-Options DENY, X-Content-Type-Options nosniff via nitro routeRules
- GCS upload: use `upload_file_to_gcs()` from `utils.py` — shared by linkedin_scanner and followup_scorer
- Bug report: user-controlled screenshots only (paste/upload) — NEVER use DOM-scraping libraries (html2canvas etc.)
- Bug report API: sanitize all user input, wrap text in code blocks, validate screenshot as image data URL
- GITHUB_TOKEN on Render: fine-grained PAT, Issues RW only, scoped to repo, 90-day expiry

## LinkedIn Scanning
- `scan_batch.py` helper: `pending` (show unscanned), `record` (save signal), `stats` (counts by type), `upload` (push to GCS)
- Browser automation: navigate to profile → `get_page_text` → parse name/headline/company → detect job change vs known org → record
- Rate: ~15s per profile (4s wait + extraction + recording)
- Targets generated by `python main.py linkedin-scan --skip-scan --limit N`
- `/pub/` and percent-encoded URLs often broken — `scan_batch.py pending` filters these out
- Name verification: `verify_name_match()` uses fuzzy matching (rapidfuzz) to catch URL→wrong-person mismatches
- Cloud Run env vars (as of 2026-04-14): ENABLE_FOLLOWUP_SCORING=true, ENABLE_CRM_SYNC=true, NOTIFICATION_EMAIL, OWNER_EMAIL_PERSONAL, OWNER_EMAIL_WORK, ENVIRONMENT=cloud, RESEND_API_KEY (Secret Manager), ANTHROPIC_API_KEY (Secret Manager)

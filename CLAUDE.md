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
- Cloud Build auto-deploys Cloud Run Job on push to main (~3min); trigger: `peter-fusek/contactrefiner` (GitHub App on peter-fusek account); uses `--update-env-vars` (NOT `--set-env-vars` which wipes all vars); must include `ENVIRONMENT=cloud`
- GA4 Measurement ID: `G-QFW0D3J3KV` (in nuxt.config.ts head scripts)

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
- Phase 5 gated by `ENABLE_CRM_SYNC` env var — syncs CRM notes → biographies, tags → contact groups (additive only)
- GCS is the message bus: workplan → analyze → queue → review → export → apply
- Review sessions in `data/review_sessions/`, decisions in `data/review_decisions_*.json`
- Review changeIds: hash `resourceName|field|newVal` only (NOT oldVal — it changes between re-analyses)
- Review session matching: `getSessionForReviewFile()` finds session by `reviewFilePath`, not just latest
- Review auto-save: 1s debounce + `sendBeacon` on `beforeunload` for reliable persistence
- Feedback learning in `data/feedback.jsonl`
- `readJson` returns null ONLY on 404 — throws for all other GCS errors (auth, permissions, etc.)
- Emergency stop: `data/pipeline_paused.json` (written by dashboard, checked by entrypoint.py)

## Dashboard Patterns
- Name resolution: `getContactNameMap()` in gcs.ts — resolves resourceName → displayName from workplan + LinkedIn signals; changelog.get.ts also enriches inline from changelog `names[0].displayName` entries
- CRM state: `data/crm_state.json` in GCS — stage, notes, tags per contact; `getCRMState()`/`saveCRMState()` in gcs.ts
- CRM API: GET /api/crm (merged followup+signals+state), POST /api/crm/update, POST /api/crm/batch-move
- Cache: 60s TTL in-memory Map; `clearCache()` exposed via POST /api/cache-clear
- Demo masking: `demo.ts` — must handle ALL PII fields including `field === 'contact'` (tobedeleted names)
- API sub-routes: use directory structure (e.g., `api/config/index.get.ts` + `api/config/pause.post.ts`)
- Nuxt API routes: ALL endpoints need `isDemoMode()` guard (repo is public, unauthenticated users get empty data)
- Nav order: Status, Review, CRM, Changelog, Runs, Pipeline, Config (Analytics/Social Signals/FollowUp removed from nav, pages still exist or redirect)
- Security headers: X-Frame-Options DENY, X-Content-Type-Options nosniff via nitro routeRules
- GCS upload: use `upload_file_to_gcs()` from `utils.py` — shared by linkedin_scanner and followup_scorer
- Bug report: user-controlled screenshots only (paste/upload) — NEVER use DOM-scraping libraries (html2canvas etc.)
- Bug report API: sanitize all user input, wrap text in code blocks, validate screenshot as image data URL
- GITHUB_TOKEN on Render: fine-grained PAT, Issues RW only, scoped to repo, 90-day expiry

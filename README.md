# Google Contacts Refiner

Automated Google Contacts cleaner — SK/CZ diacritics, phone normalization, deduplication, AI-powered analysis (Claude), learning memory, daily Cloud Run scheduling, and a review dashboard.

**Live:** [contactrefiner.com](https://contactrefiner.com) | [Privacy Policy](https://contactrefiner.com/privacy)

## Features

| Category | Description |
|----------|-------------|
| **Diacritics** | Auto-fix Slovak/Czech names (800+ dictionary entries, suffix patterns, memory-learned prefs) |
| **Phone numbers** | Normalize to international format (+421, +420, ...), type detection |
| **Emails** | Validation, lowercase, duplicate detection, generic email flagging |
| **Addresses** | ZIP code formatting, country detection, shared address detection |
| **Organizations** | Name casing, domain-based org extraction |
| **Duplicates** | Fuzzy detection by name, phone, email |
| **Enrichment** | Structured data extraction from notes (phones, emails, URLs, birthdays) |
| **LinkedIn** | Connection matching and enrichment from LinkedIn export |
| **AI Analysis** | Claude-powered smart suggestions for ambiguous changes |
| **Memory** | Cross-session learning — remembers your approval/rejection patterns |
| **Activity Tagging** | Gmail + Calendar scanning to label contacts by last interaction year |
| **Review Dashboard** | Web UI for reviewing and approving suggested changes |
| **Cloud Run** | Docker-based daily job on GCP with queue stats tracking |
| **Safety** | Full backup/restore, changelog, rollback, 3-phase pipeline |

## Pipeline

The refiner runs a three-phase pipeline:

1. **Phase 0** — Auto-export any pending review decisions, process feedback into memory
2. **Phase 1** (fast, ~5 min) — Backup, analyze (rule-based, no AI), auto-fix HIGH confidence changes, record queue stats
3. **Phase 2** (slow, checkpointed) — AI review of MEDIUM confidence changes, apply promoted fixes

Confidence levels:
- **HIGH** (>= 0.90) — dictionary matches, learned preferences, exact patterns
- **MEDIUM** (>= 0.60) — suffix patterns, fuzzy matching
- **LOW** (< 0.60) — speculative suggestions (not applied)

## Dashboard

The review dashboard at [contactrefiner.com/dashboard](https://contactrefiner.com/dashboard) provides:
- Review queue with approve/reject/edit per change
- Queue stats trend chart (90-day rolling window)
- Session history with decision counts
- Auto-export when 100% reviewed

Built with Nuxt 4, Nuxt UI 4, and Tailwind CSS 4.2. Hosted on Render.

## Quick Start

```bash
# Clone
git clone https://github.com/peter-fusek/google-contacts-refiner.git
cd google-contacts-refiner

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Dependencies
pip install -r requirements.txt
```

### Google API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable **People API**
3. Create OAuth 2.0 credentials (Desktop App)
4. Download `credentials.json` to the project root

### Environment Variables

```bash
# .env (gitignored)
ANTHROPIC_API_KEY=sk-ant-...   # Required for AI analysis
ENVIRONMENT=local              # "local" (default) or "cloud"
```

## Usage

```bash
python main.py auth         # Authenticate and test connection
python main.py backup       # Create contacts backup
python main.py analyze      # Analyze contacts, generate fix plan
python main.py fix          # Apply fixes (interactive batch approval)
python main.py fix --auto   # Apply high-confidence fixes automatically
python main.py verify       # Verify changes against backup
python main.py rollback     # Revert changes from changelog
python main.py resume       # Continue interrupted session
python main.py info         # Show session/backup/plan info
```

## Architecture

```
main.py                  CLI entry point
entrypoint.py            Cloud Run Job entry point (3-phase pipeline)
├── config.py            Configuration, environment detection
├── auth.py              OAuth2 auth (local token / Secret Manager)
├── api_client.py        Google People API wrapper (rate limiting, retry)
├── backup.py            Backup/restore contacts
├── analyzer.py          Analysis orchestration, confidence adjustment
│   ├── normalizer.py    Field normalization (diacritics, phones, emails, addresses)
│   ├── enricher.py      Data extraction from notes
│   ├── deduplicator.py  Duplicate detection
│   └── labels_manager.py Group/label management
├── ai_analyzer.py       Claude AI integration (smart suggestions)
├── memory.py            Learning system (25 rule categories, Bayesian confidence)
├── activity.py          Gmail + Calendar activity tagging
├── linkedin_matcher.py  LinkedIn connection matching
├── workplan.py          Batch generation for approval
├── batch_processor.py   Interactive batch processing
├── changelog.py         Change tracking (append-only JSONL)
├── recovery.py          Session recovery after interruption
├── notifier.py          Notifications (macOS / Cloud Logging)
└── code_tables/         Diacritics dictionary, surname suffixes
```

## Cloud Deployment

The refiner runs as a **Cloud Run Job** on Google Cloud, triggered daily by Cloud Scheduler.

```
Cloud Scheduler ──(daily 9:00 CET)──> Cloud Run Job
                                        │
                    ┌───────────────────┼───────────┐
                    │                   │           │
               Secret Manager    GCS Bucket    Cloud Logging
               (refresh token    (data/         (structured)
                + API key)       backups)
                    │                   │
                    └──> Python app  <──┘
                          │         │
                People API▼         ▼ Anthropic
               (gmail.com)       (Claude AI)
```

**Infrastructure:**
- **Project:** `contacts-refiner` (europe-west1)
- **Storage:** GCS bucket `contacts-refiner-data` (volume-mounted via GCS FUSE)
- **Secrets:** Secret Manager (API keys, refresh tokens)
- **CI/CD:** Cloud Build from GitHub → Artifact Registry → Cloud Run update
- **Dashboard:** Render (Starter plan), auto-deploys on push
- **Domain:** contactrefiner.com (WebSupport.sk)
- **Cost:** ~$0/month GCP (free tier) + ~$3/day Anthropic API + $7/month Render

## License

Private project.

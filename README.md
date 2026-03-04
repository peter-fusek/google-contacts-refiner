# Google Contacts Refiner

Automated Google Contacts cleaner — SK/CZ diacritics, phone normalization, deduplication, AI-powered analysis (Claude), headless scheduling. Runs locally or as a Cloud Run Job.

Automatizovaný nástroj na čistenie a opravu Google Kontaktov s dôrazom na slovenské a české mená, diakritiku, telefónne čísla a detekciu duplikátov.

## Features

| Category | Description |
|----------|-------------|
| **Diacritics** | Auto-fix Slovak/Czech names (600+ dictionary entries) |
| **Phone numbers** | Normalize to international format (+421, +420, ...) |
| **Emails** | Validation, lowercase, format cleanup |
| **Addresses** | ZIP code formatting, country detection |
| **Organizations** | Name unification |
| **Duplicates** | Fuzzy detection by name, phone, email |
| **Enrichment** | Structured data extraction from notes/bios |
| **AI Analysis** | Claude-powered smart suggestions (v1.1+) |
| **Memory** | Cross-session learning from past decisions (v1.1+) |
| **Headless mode** | `--auto` flag for unattended runs (v1.2+) |
| **Cloud Run** | Docker-based daily job on GCP (v1.4+) |
| **Safety** | Full backup/restore, changelog, rollback |

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

### Typical Workflow

```bash
python main.py auth         # 1. Login
python main.py backup       # 2. Backup (always before fixes!)
python main.py analyze      # 3. Analyze — finds issues, creates plan
python main.py fix          # 4. Fix — approve in batches (~50 contacts)
python main.py verify       # 5. Verify — compare with backup
```

## Architecture

```
main.py                  CLI entry point
├── config.py            Configuration, environment detection (local/cloud)
├── auth.py              OAuth2 auth (local token.json / cloud Secret Manager)
├── api_client.py        Google People API wrapper (rate limiting, retry)
├── backup.py            Backup/restore contacts
├── analyzer.py          Analysis orchestration
│   ├── normalizer.py    Field normalization (diacritics, phones, emails, addresses)
│   ├── enricher.py      Data extraction from notes
│   ├── deduplicator.py  Duplicate detection
│   └── labels_manager.py Group/label management
├── ai_analyzer.py       Claude AI integration (smart suggestions)
├── memory.py            Cross-session learning (file-based)
├── workplan.py          Batch generation for approval
├── batch_processor.py   Interactive batch processing
├── changelog.py         Change tracking (append-only JSONL)
├── recovery.py          Session recovery after interruption
├── notifier.py          Notifications (macOS / Cloud Logging)
├── instructions.md      Human-editable rules (version controlled)
└── utils.py             Helper functions
```

## Cloud Deployment (v1.4)

The refiner runs as a **Cloud Run Job** on Google Cloud, triggered daily by Cloud Scheduler.

```
Cloud Scheduler ──(daily 9:00 CET)──▶ Cloud Run Job
                                       │
                    ┌──────────────────┼───────────┐
                    │                  │           │
               Secret Manager    GCS Bucket    Cloud Logging
               (refresh token    (data/         (structured)
                + API key)       backups)
                    │                  │
                    └───▶ Python app ◄─┘
                          │         │
                People API▼         ▼ Anthropic
               (gmail.com)       (Claude AI)
```

**Infrastructure:**
- **Project:** `contacts-refiner` (Google Cloud, instarea.sk org)
- **Region:** `europe-west1` (Belgium)
- **Storage:** GCS bucket `contacts-refiner-data` (volume-mounted at `/mnt/data`)
- **Secrets:** Secret Manager (`anthropic-api-key`, `contacts-refresh-token`)
- **CI/CD:** Cloud Build from GitHub → Artifact Registry → Cloud Run update
- **Cost:** ~$0/month GCP (free tier) + ~$10-15 Anthropic API

## Confidence Scoring

Every proposed change has a confidence score:
- **HIGH** (≥ 0.90) — exact matches, dictionary lookups
- **MEDIUM** (≥ 0.60) — pattern matches, fuzzy matching
- **LOW** (< 0.60) — speculative suggestions

In `--auto` mode, only HIGH confidence changes are applied.

## Data

All runtime data is stored in `data/` (gitignored):
- `backup_TIMESTAMP.json` — full contacts backup
- `workplan_TIMESTAMP.json` — batch plan for approval
- `changelog_TIMESTAMP.jsonl` — audit trail
- `checkpoint.json` — current session state
- `memory.json` — learned patterns from past sessions

## Versioning

| Version | Milestone | Description |
|---------|-----------|-------------|
| v1.0.0 | Initial release | Core refiner: normalization, deduplication, batch approval |
| v1.1.0 | AI Integration | Claude AI analysis + file-based memory system |
| v1.2.0 | Automation | Headless mode, macOS notifications, launchd scheduling |
| v1.3.0 | Gmail/Calendar | *(planned)* Optional Gmail + Calendar API enrichment |
| v1.4.0 | Cloud Migration | *(in progress)* Cloud Run Jobs, GCS, Secret Manager, CI/CD |

## License

Private project.

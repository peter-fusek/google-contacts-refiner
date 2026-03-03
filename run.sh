#!/bin/bash
# Google Contacts Refiner — Daily Scheduled Run
# Used by launchd for automated execution on macOS.
#
# Install:
#   cp com.user.contacts-refiner.plist ~/Library/LaunchAgents/
#   launchctl load ~/Library/LaunchAgents/com.user.contacts-refiner.plist
#
# Manual test:
#   ./run.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$PROJECT_DIR/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$LOG_DIR"

# Activate virtual environment
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
else
    echo "ERROR: Virtual environment not found at $PROJECT_DIR/.venv"
    exit 1
fi

cd "$PROJECT_DIR"

# Load environment variables from .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"

echo "=== Google Contacts Refiner — $(date) ===" >> "$LOG_FILE"

# Step 1: Backup
echo "--- Backup ---" >> "$LOG_FILE"
python main.py backup >> "$LOG_FILE" 2>&1 || {
    echo "ERROR: Backup failed" >> "$LOG_FILE"
    osascript -e 'display notification "Backup zlyhal!" with title "Contacts Refiner"' 2>/dev/null || true
    exit 1
}

# Step 2: Analyze
echo "--- Analyze ---" >> "$LOG_FILE"
python main.py analyze >> "$LOG_FILE" 2>&1 || {
    echo "ERROR: Analysis failed" >> "$LOG_FILE"
    osascript -e 'display notification "Analýza zlyhala!" with title "Contacts Refiner"' 2>/dev/null || true
    exit 1
}

# Step 3: Auto-fix (high confidence only)
echo "--- Fix (auto) ---" >> "$LOG_FILE"
python main.py fix --auto --confidence 0.90 >> "$LOG_FILE" 2>&1 || {
    echo "ERROR: Auto-fix failed" >> "$LOG_FILE"
    osascript -e 'display notification "Auto-fix zlyhal!" with title "Contacts Refiner"' 2>/dev/null || true
    exit 1
}

echo "=== Done — $(date) ===" >> "$LOG_FILE"

# Cleanup old logs (keep 30 days)
find "$LOG_DIR" -name "run_*.log" -mtime +30 -delete 2>/dev/null || true

#!/bin/bash
# Install harvester launchd agents on macOS.
#
# Copies launchagents/*.plist → ~/Library/LaunchAgents/ with user-specific
# path rewrites, loads them via launchctl, and drops a newsyslog.d entry
# to rotate harvester logs (keeps ~/Library/Logs/contactrefiner bounded).
#
# SAFETY — dry-run by default.
#   ./scripts/install-launchd.sh            → prints what it WOULD do, no changes
#   ./scripts/install-launchd.sh --apply    → actually copies, loads, rotates
#   ./scripts/install-launchd.sh --uninstall → unload + remove plists (no trash)
#
# Idempotent under --apply: unload existing agents before reinstalling.
#
# Deferred review items from #151 rolled in here:
#   - reachability probe: harvester/beeper_oauth.is_beeper_reachable() is called
#     at every harvester entry point; this script doesn't need an extra hook
#   - pipeline_paused check: harvester/pipeline.is_harvester_paused() fires on
#     every run; no script-level work needed
#   - log rotation via newsyslog.d — handled below

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/Library/Logs/contactrefiner"
UV_BIN="$(command -v uv || echo /opt/homebrew/bin/uv)"
NEWSYSLOG_DIR="/etc/newsyslog.d"
NEWSYSLOG_FILE="${NEWSYSLOG_DIR}/contactrefiner-harvester.conf"

AGENTS=(
  "com.contactrefiner.harvester.hourly"
  "com.contactrefiner.harvester.daily"
  "com.contactrefiner.harvester.weekly"
  "com.contactrefiner.harvester.monthly"
)

MODE="dry-run"
for arg in "$@"; do
  case "$arg" in
    --apply)    MODE="apply" ;;
    --uninstall) MODE="uninstall" ;;
    --help|-h)
      echo "Usage: $0 [--apply | --uninstall | --help]" >&2
      exit 0 ;;
    *)
      echo "error: unknown flag: $arg" >&2
      echo "run with --help for usage" >&2
      exit 2 ;;
  esac
done

log() { echo "[install-launchd] $*"; }

# Pre-flight checks — run even in dry-run so the user sees real blockers upfront.
if [[ ! -x "${UV_BIN}" ]]; then
  log "error: uv not found. Install with: brew install uv"
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/main.py" ]]; then
  log "error: main.py not found at ${REPO_ROOT}/main.py"
  exit 1
fi

# Verify all plists exist BEFORE claiming we're ready.
MISSING=()
for agent in "${AGENTS[@]}"; do
  if [[ ! -f "${REPO_ROOT}/launchagents/${agent}.plist" ]]; then
    MISSING+=("${agent}.plist")
  fi
done
if (( ${#MISSING[@]} > 0 )); then
  log "error: missing plist templates: ${MISSING[*]}"
  exit 1
fi

# ── uninstall path ────────────────────────────────────────────────────────
if [[ "${MODE}" == "uninstall" ]]; then
  for agent in "${AGENTS[@]}"; do
    dst="${LAUNCH_DIR}/${agent}.plist"
    if [[ -f "${dst}" ]]; then
      launchctl unload "${dst}" 2>/dev/null || true
      rm -f "${dst}"
      log "✓ removed ${agent}"
    else
      log "  ${agent} not installed — skipping"
    fi
  done
  log "Uninstall complete. newsyslog entry left in place at ${NEWSYSLOG_FILE}"
  log "  (remove manually if desired — requires sudo)"
  exit 0
fi

# ── plan / dry-run output ─────────────────────────────────────────────────
log "mode: ${MODE}"
log "repo: ${REPO_ROOT}"
log "uv:   ${UV_BIN}"
log "logs: ${LOG_DIR}"
log ""
log "would install these agents into ${LAUNCH_DIR}:"
for agent in "${AGENTS[@]}"; do
  log "  - ${agent}.plist"
done
log ""
log "would write newsyslog.d rotation config to:"
log "  ${NEWSYSLOG_FILE}  (requires sudo)"
log ""

if [[ "${MODE}" == "dry-run" ]]; then
  log "dry-run only — re-run with --apply to actually install."
  exit 0
fi

# ── apply path ────────────────────────────────────────────────────────────

mkdir -p "${LAUNCH_DIR}"
mkdir -p "${LOG_DIR}"

# Escape `&` in replacement strings so sed treats them as literal characters.
# Without this, a REPO_ROOT containing `&` (e.g. ~/Projects/R&D/…) would
# interpolate "the matched text" from the left-hand side into the replacement,
# silently corrupting the generated plist — and launchctl would load it.
SAFE_ROOT="${REPO_ROOT//&/\\&}"
SAFE_LOG="${LOG_DIR//&/\\&}"
SAFE_UV="${UV_BIN//&/\\&}"

for agent in "${AGENTS[@]}"; do
  src="${REPO_ROOT}/launchagents/${agent}.plist"
  dst="${LAUNCH_DIR}/${agent}.plist"

  # Unload if already loaded (idempotent reinstall)
  launchctl unload "${dst}" 2>/dev/null || true

  # Rewrite hardcoded paths in the template to match invoking user.
  # Pipe delimiter avoids needing to escape slashes in paths.
  sed -e "s|/Users/peterfusek1980gmail.com/Projects/contactrefiner|${SAFE_ROOT}|g" \
      -e "s|/Users/peterfusek1980gmail.com/Library/Logs/contactrefiner|${SAFE_LOG}|g" \
      -e "s|/opt/homebrew/bin/uv|${SAFE_UV}|g" \
      "${src}" > "${dst}"

  launchctl load "${dst}"
  log "✓ loaded ${agent}"
done

# ── log rotation (newsyslog.d) ────────────────────────────────────────────
# Rotate each .log when it hits 5MB, keep 4 rotations gzipped. Uses * in path
# so both .log (stdout) and .err (stderr) get rotated.
#
# Requires sudo because /etc/newsyslog.d is root-owned. We write to a temp
# file first and use `sudo install` so failure leaves no half-configured state.
# Write the newsyslog template to a stable path under the repo so the user
# can run the `sudo install` step at their convenience without hunting for a
# deleted /tmp file.
NS_STAGING="${LOG_DIR}/newsyslog.d-contactrefiner-harvester.conf"
cat > "${NS_STAGING}" <<EOF
# Rotate ContactRefiner harvester logs.
# Installed by scripts/install-launchd.sh — edit there, not here.
# Format: logfile_name  [owner:group]  mode  count  size  when  flags
${LOG_DIR}/harvester-*.log   $(whoami):staff  644   4    5120    *     GZ
${LOG_DIR}/harvester-*.err   $(whoami):staff  644   4    5120    *     GZ
EOF

if sudo -n true 2>/dev/null; then
  sudo install -m 0644 "${NS_STAGING}" "${NEWSYSLOG_FILE}"
  log "✓ wrote ${NEWSYSLOG_FILE}"
else
  log "ℹ  newsyslog.d config staged at ${NS_STAGING}"
  log "   run: sudo install -m 0644 ${NS_STAGING} ${NEWSYSLOG_FILE}"
  log "   (sudo not available non-interactively; skipped)"
fi

log ""
log "Installed ${#AGENTS[@]} launch agents."
log "Logs: ${LOG_DIR}/harvester-*.log (rotated via newsyslog.d)"
log "Uninstall: ./scripts/install-launchd.sh --uninstall"

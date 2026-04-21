#!/bin/bash
# Uninstall harvester launchd agents. Unloads + removes plists from
# ~/Library/LaunchAgents/. Logs at ~/Library/Logs/contactrefiner/ are left
# in place (consistent with project deletion policy: never permanently
# delete user data, 30-day retention applies).

set -euo pipefail

LAUNCH_DIR="${HOME}/Library/LaunchAgents"

AGENTS=(
  "com.contactrefiner.harvester.hourly"
  "com.contactrefiner.harvester.daily"
  "com.contactrefiner.harvester.weekly"
  "com.contactrefiner.harvester.monthly"
)

for agent in "${AGENTS[@]}"; do
  dst="${LAUNCH_DIR}/${agent}.plist"

  if [[ ! -f "${dst}" ]]; then
    echo "skip: ${agent} not installed"
    continue
  fi

  launchctl unload "${dst}" 2>/dev/null || true
  rm "${dst}"
  echo "✓ removed ${agent}"
done

echo ""
echo "Uninstalled. Logs preserved at ~/Library/Logs/contactrefiner/"

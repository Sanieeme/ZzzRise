#!/bin/bash
set -euo pipefail
LABEL="com.zzzrise.etl"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
echo "ZzzRise automatic ETL schedule has been removed."

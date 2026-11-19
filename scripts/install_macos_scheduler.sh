#!/bin/bash
set -euo pipefail

# Install a per-user macOS launchd job that runs ZzzRise ETL every day at 00:00.
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LABEL="com.zzzrise.etl"
PLIST_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$PROJECT_DIR/logs"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"

if [[ ! -x "$PYTHON" ]]; then
  echo "Could not find the project's virtual-environment Python: $PYTHON" >&2
  echo "First run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"
export ZZZRISE_PROJECT_DIR="$PROJECT_DIR"
export ZZZRISE_PYTHON="$PYTHON"
export ZZZRISE_STDOUT="$LOG_DIR/etl.log"
export ZZZRISE_STDERR="$LOG_DIR/etl-error.log"
export ZZZRISE_PLIST_PATH="$PLIST_PATH"

python3 - <<'PYTHON'
import os
import plistlib
from pathlib import Path

config = {
    "Label": "com.zzzrise.etl",
    "ProgramArguments": [os.environ["ZZZRISE_PYTHON"], "-m", "pipeline.run_pipeline"],
    "WorkingDirectory": os.environ["ZZZRISE_PROJECT_DIR"],
    "StartCalendarInterval": {"Hour": 0, "Minute": 0},
    "StandardOutPath": os.environ["ZZZRISE_STDOUT"],
    "StandardErrorPath": os.environ["ZZZRISE_STDERR"],
    "RunAtLoad": False,
}
with Path(os.environ["ZZZRISE_PLIST_PATH"]).open("wb") as handle:
    plistlib.dump(config, handle)
PYTHON

# Replace an older copy of this same job, then load the new one.
launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

# Trigger one run now to verify installation immediately.
launchctl kickstart "gui/$(id -u)/$LABEL"

echo "ZzzRise ETL scheduler installed."
echo "Schedule: every day at 00:00 (Mac local time)."
echo "Logs: $LOG_DIR/etl.log and $LOG_DIR/etl-error.log"
echo "The Mac must be powered on and awake at the scheduled time."

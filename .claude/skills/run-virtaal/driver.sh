#!/usr/bin/env bash
# Driver for launching, screenshotting, and stopping Virtaal (GTK3 desktop
# app) on macOS. See SKILL.md in this directory for usage.
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
VENV="$REPO_ROOT/.venv"
PIDFILE="/tmp/virtaal-driver.pid"
LOGFILE="/tmp/virtaal-driver.log"

setup() {
  if [ ! -d "$VENV" ]; then
    python3 -m venv --system-site-packages "$VENV"
  fi
  "$VENV/bin/python3" -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" \
    || { echo "PyGObject/GTK3 not found on the system Python — install with: brew install pygobject3 gtk+3" >&2; exit 1; }
  "$VENV/bin/python3" -c "import translate" 2>/dev/null || "$VENV/bin/pip" install translate-toolkit
  "$VENV/bin/python3" -c "import pycurl" 2>/dev/null || "$VENV/bin/pip" install pycurl
  "$VENV/bin/python3" -c "import diff_match_patch" 2>/dev/null || "$VENV/bin/pip" install diff-match-patch
  "$VENV/bin/python3" -c "import cheroot" 2>/dev/null || "$VENV/bin/pip" install cheroot
  "$VENV/bin/python3" -c "import enchant" 2>/dev/null || "$VENV/bin/pip" install pyenchant
}

launch() {
  local file="${1:-}"
  cd "$REPO_ROOT"
  # shellcheck disable=SC2086
  nohup env PYTHONPATH=. "$VENV/bin/python3" bin/virtaal $file > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  disown
  echo "launched pid $(cat "$PIDFILE"), log at $LOGFILE"
}

screenshot() {
  local out="${1:-/tmp/virtaal-screenshot.png}"
  screencapture -x "$out"
  echo "$out"
}

alive() {
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1
}

quit() {
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null
  fi
  rm -f "$PIDFILE"
}

log() {
  cat "$LOGFILE" 2>/dev/null
}

case "${1:-}" in
  setup) setup ;;
  launch) launch "${2:-}" ;;
  screenshot) screenshot "${2:-}" ;;
  alive) if alive; then echo yes; else echo no; fi ;;
  quit) quit ;;
  log) log ;;
  *)
    echo "usage: $0 {setup|launch [file]|screenshot [path]|alive|quit|log}" >&2
    exit 1
    ;;
esac

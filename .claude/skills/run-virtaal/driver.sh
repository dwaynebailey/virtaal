#!/usr/bin/env bash
# Driver for launching, screenshotting, stopping, and (accessibility
# permission permitting) actually driving Virtaal (GTK3 desktop app) on
# macOS. See SKILL.md in this directory for usage.
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SKILL_DIR/../../.." && pwd)"
VENV="$REPO_ROOT/.venv"
PIDFILE="/tmp/virtaal-driver.pid"
LOGFILE="/tmp/virtaal-driver.log"

# The process System Events sees is "Python" (Homebrew's framework Python
# self-relaunches through its own Python.app when it opens windows - see
# ISSUE_TRIAGE.md's ".app bundle" entries), not "Virtaal" or "virtaal" -
# confirmed empirically, 2026-08-23. Overridable in case a future fix to
# that branding issue changes it, or when driving a frozen .app build
# instead of a dev-checkout launch.
AX_PROCESS="${VIRTAAL_AX_PROCESS:-Python}"

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

# ACCESSIBILITY-DRIVEN COMMANDS #
# Everything below needs the terminal/IDE running this script to have
# Accessibility permission for System Events (System Settings > Privacy &
# Security > Accessibility). Without it these fail with "osascript is not
# allowed assistive access" (-25211) rather than silently no-op'ing.
#
# Confirmed live, 2026-08-23, once that permission was actually granted:
# System Events *can* see and drive this app's GTK-on-quartz window and
# menu bar just fine - `count windows`, menu bar item enumeration, and
# `click` all work. The long-standing assumption in this skill that
# GTK-on-quartz windows aren't reliably exposed to the Accessibility API
# was really just describing the missing-permission state, not a real
# GTK/quartz limitation - don't re-assume it's broken without first
# checking permission is actually granted.

geometry() {
  osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to tell (item 1 of windows) to return {position, size}"
}

window_title() {
  osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to return name of (item 1 of windows)"
}

resize() {
  local w="${1:?usage: resize <width> <height>}" h="${2:?usage: resize <width> <height>}"
  osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to set size of (item 1 of windows) to {$w, $h}"
}

move() {
  local x="${1:?usage: move <x> <y>}" y="${2:?usage: move <x> <y>}"
  osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to set position of (item 1 of windows) to {$x, $y}"
}

# Drives a burst of rapid resize events (shrink then grow - the same
# live-drag-like pattern that reproduced the 2026-08-23 Windows
# runaway-resize bug: many configure-events per user gesture, not one)
# and then samples the window size several times with *no* further
# input. A genuine feedback loop shows up as the sampled sizes drifting
# on their own even though nothing is telling the window to resize
# any more - see storetreeview.py's on_configure_event() history for
# why that's specifically what this check is for. Prints PASS/FAIL and
# exits non-zero on FAIL, so it's usable as a gate, not just a report.
resize_check() {
  local out
  out=$(osascript <<APPLESCRIPT
tell application "System Events"
    if not (exists process "$AX_PROCESS") then return "ERROR:no-process"
    tell process "$AX_PROCESS"
        if (count of windows) = 0 then return "ERROR:no-window"
        set w to item 1 of windows
        set {origX, origY} to position of w
        set {origW, origH} to size of w

        repeat with i from 1 to 10
            set size of w to {origW - i * 5, origH - i * 3}
            delay 0.12
        end repeat
        repeat with i from 1 to 15
            set size of w to {origW - 100 + i * 8, origH - 60 + i * 5}
            delay 0.1
        end repeat
        delay 0.3

        set sizes to {}
        repeat 5 times
            set {sw, sh} to size of w
            copy ((sw as string) & "x" & (sh as string)) to end of sizes
            delay 1
        end repeat

        set size of w to {origW, origH}

        set AppleScript's text item delimiters to ","
        set sizesStr to sizes as string
        set AppleScript's text item delimiters to ""
        return "OK:" & sizesStr
    end tell
end tell
APPLESCRIPT
)
  case "$out" in
    ERROR:*)
      echo "FAIL: $out" >&2
      return 1
      ;;
    OK:*)
      local samples="${out#OK:}"
      local first="${samples%%,*}"
      IFS=',' read -ra parts <<< "$samples"
      for s in "${parts[@]}"; do
        if [ "$s" != "$first" ]; then
          echo "FAIL: window size drifted with no input - samples: $samples" >&2
          return 1
        fi
      done
      echo "PASS: settled at $first, stable across ${#parts[@]} samples"
      return 0
      ;;
    *)
      echo "FAIL: unexpected osascript output: $out" >&2
      return 1
      ;;
  esac
}

# `keystroke`/`key code` are NOT scoped by the `tell process "X"` block
# the way `click`/property reads are - System Events sends them to
# whatever process is actually frontmost at the OS level, regardless of
# which process's accessibility tree you're addressing. Confirmed live,
# 2026-08-23: sending `keystroke Z` without activating Virtaal first
# produced no visible effect at all - not even an error, just silently
# went wherever actually had focus (almost certainly the calling
# terminal). Activate the target process first, every time, rather than
# assuming it already has focus.
_activate() {
  osascript -e "tell application \"System Events\" to set frontmost of process \"$AX_PROCESS\" to true"
}

# Sends a physical keystroke, e.g.:
#   keystroke z command          -> Cmd+Z
#   keystroke "Down" "control"   -> Ctrl+Down (use key-code form for
#                                    non-character keys; System Events'
#                                    `keystroke` needs the literal glyph,
#                                    "Down" arrow works as a named key
#                                    only via `key code`, see key-code)
keystroke_cmd() {
  local key="${1:?usage: keystroke <key> [modifier ...]}"; shift
  local mods=""
  for m in "$@"; do
    mods="${mods}${mods:+, }${m} down"
  done
  _activate
  if [ -n "$mods" ]; then
    osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to keystroke \"$key\" using {$mods}"
  else
    osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to keystroke \"$key\""
  fi
}

# Sends a key by its numeric key code, needed for arrow keys/Page Up/
# Page Down/Escape etc. which `keystroke` can't address by name. Common
# codes: Up=126 Down=125 Left=123 Right=124 PageUp=116 PageDown=121
# Escape=53.
key_code_cmd() {
  local code="${1:?usage: key-code <code> [modifier ...]}"; shift
  local mods=""
  for m in "$@"; do
    mods="${mods}${mods:+, }${m} down"
  done
  _activate
  if [ -n "$mods" ]; then
    osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to key code $code using {$mods}"
  else
    osascript -e "tell application \"System Events\" to tell process \"$AX_PROCESS\" to key code $code"
  fi
}

# Lists the item names under one top-level menu, e.g. `menu-items File`
# -> "Open, Save, Save As, ...". Useful for asserting menu structure
# didn't silently change, without needing a screenshot.
menu_items() {
  local top="${1:?usage: menu-items <TopMenuLabel>}"
  osascript <<APPLESCRIPT
tell application "System Events"
    tell process "$AX_PROCESS"
        set target to menu bar item "$top" of menu bar 1
        click target
        delay 0.25
        set itemNames to name of every menu item of menu 1 of target
        key code 53
        return itemNames
    end tell
end tell
APPLESCRIPT
}

# Clicks through a menu path, e.g. `click-menu Navigation Down` clicks
# Navigation then Down within it. Each segment after the first descends
# one level (menu 1 of the previously-clicked item), so this also
# reaches sub-submenus given more segments.
click_menu() {
  if [ "$#" -lt 1 ]; then
    echo "usage: $0 click-menu <TopMenu> [SubItem ...]" >&2
    return 1
  fi
  local top="$1"; shift
  local script="tell application \"System Events\"
    tell process \"$AX_PROCESS\"
        set target to menu bar item \"$top\" of menu bar 1
        click target"
  for seg in "$@"; do
    script="${script}
        delay 0.25
        set target to menu item \"$seg\" of menu 1 of target
        click target"
  done
  script="${script}
        return \"clicked\"
    end tell
end tell"
  osascript -e "$script"
}

case "${1:-}" in
  setup) setup ;;
  launch) launch "${2:-}" ;;
  screenshot) screenshot "${2:-}" ;;
  alive) if alive; then echo yes; else echo no; fi ;;
  quit) quit ;;
  log) log ;;
  geometry) geometry ;;
  window-title) window_title ;;
  resize) resize "${2:-}" "${3:-}" ;;
  move) move "${2:-}" "${3:-}" ;;
  resize-check) resize_check ;;
  keystroke) shift; keystroke_cmd "$@" ;;
  key-code) shift; key_code_cmd "$@" ;;
  menu-items) menu_items "${2:-}" ;;
  click-menu) shift; click_menu "$@" ;;
  *)
    echo "usage: $0 {setup|launch [file]|screenshot [path]|alive|quit|log|" >&2
    echo "            geometry|window-title|resize W H|move X Y|resize-check|" >&2
    echo "            keystroke KEY [MODIFIER...]|key-code CODE [MODIFIER...]|" >&2
    echo "            menu-items TOPMENU|click-menu TOPMENU [SUBITEM...]}" >&2
    exit 1
    ;;
esac

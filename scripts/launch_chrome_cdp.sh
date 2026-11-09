#!/usr/bin/env bash
# Launch a DEDICATED Chrome/Chromium instance for browser-harness automation.
#
# Why a separate instance: `--remote-debugging-port` is refused on the user's
# normal profile (Chrome 136+), and attaching to a personal profile would expose
# personal cookies to automation. This profile is separate and disposable.
#
# Security: CDP binds to 127.0.0.1 only. Never change that to 0.0.0.0 -- an open
# CDP port is unauthenticated remote control of the browser.
#
# Usage: scripts/launch_chrome_cdp.sh [--port 9222] [--profile DIR] [--headless] [--quiet]
set -euo pipefail

PORT="${CAI_CHROME_PORT:-9222}"
PROFILE="${CAI_CHROME_PROFILE:-$HOME/.cai/chrome-profile}"
HEADLESS=0
QUIET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --headless) HEADLESS=1; shift ;;
    --quiet) QUIET=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -n "${CAI_CHROME:-}" ]]; then
  CHROME="$CAI_CHROME"
else
  case "$(uname -s)" in
    Darwin)
      CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      [[ -x "$CHROME" ]] || CHROME="/Applications/Chromium.app/Contents/MacOS/Chromium"
      ;;
    Linux)
      for candidate in google-chrome google-chrome-stable chromium chromium-browser; do
        if command -v "$candidate" >/dev/null 2>&1; then CHROME="$(command -v "$candidate")"; break; fi
      done
      ;;
    *) CHROME="" ;;
  esac
fi

if [[ -z "${CHROME:-}" || ! -x "$CHROME" ]]; then
  echo "no Chrome/Chromium binary found; set CAI_CHROME=/path/to/chrome" >&2
  exit 2
fi

if curl -sS -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "a CDP endpoint already answers on 127.0.0.1:$PORT"
  curl -sS -m 5 "http://127.0.0.1:$PORT/json/version" | head -c 300; echo
  echo
  echo "reuse it with:  export BU_CDP_URL=http://127.0.0.1:$PORT"
  exit 0
fi

mkdir -p "$PROFILE" "$(dirname "$PROFILE")/logs"
LOG="$(dirname "$PROFILE")/logs/chrome.log"

ARGS=(
  "--remote-debugging-address=127.0.0.1"
  "--remote-debugging-port=$PORT"
  "--user-data-dir=$PROFILE"
  --no-first-run --no-default-browser-check
  --disable-features=Translate,MediaRouter
  # Keep audio playing in a background tab; capture needs real playback.
  --disable-background-timer-throttling
  --disable-backgrounding-occluded-windows
  --disable-renderer-backgrounding
  about:blank
)
if [[ "$HEADLESS" == "1" ]]; then
  ARGS=("--headless=new" "${ARGS[@]}")
fi

nohup "$CHROME" "${ARGS[@]}" >"$LOG" 2>&1 &
echo "launched $CHROME (pid $!) port=$PORT profile=$PROFILE"
[[ "$QUIET" == "1" ]] || echo "log: $LOG"

for _ in $(seq 1 40); do
  if curl -sS -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "CDP ready on 127.0.0.1:$PORT"
    echo "export BU_CDP_URL=http://127.0.0.1:$PORT"
    curl -sS -m 5 "http://127.0.0.1:$PORT/json/version" | head -c 200; echo
    exit 0
  fi
  sleep 0.5
done

echo "CDP did not come up on 127.0.0.1:$PORT; see $LOG" >&2
tail -20 "$LOG" >&2 || true
exit 1

#!/usr/bin/env bash
# Report whether this host can run the pipeline. Read-only: no capture, no
# writes to the database. Exit 0 when nothing blocking was found.
#
# Usage: scripts/doctor.sh [--db PATH] [--llm-check] [--fixture]
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v "${CAI_PYTHON:-python3}")"
DB="data/creator_intel.sqlite3"
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    --llm-check) EXTRA+=(--llm-check); shift ;;
    --help|-h) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

FAILED=0
step() { printf '\n== %s\n' "$1"; }
check() { if [[ "$1" == "0" ]]; then echo "  [ok  ] $2"; else echo "  [FAIL] $2"; FAILED=1; fi; }

cd "$ROOT" || exit 2

step "toolchain"
for tool in ffmpeg ffprobe browser-harness git; do
  if command -v "$tool" >/dev/null 2>&1; then check 0 "$tool -> $(command -v "$tool")"
  else check 1 "$tool not found"; fi
done
if "$PY" -c 'import faster_whisper' >/dev/null 2>&1; then check 0 "faster-whisper importable"
else check 0 "faster-whisper not installed (offline capture-only mode still works)"; fi

step "chrome cdp (browser-harness attaches here)"
CDP="${CAI_CHROME_PORT:-9222}"
if curl -sS -m 3 "http://127.0.0.1:$CDP/json/version" >/dev/null 2>&1; then
  check 0 "CDP reachable on 127.0.0.1:$CDP"
  echo "         $(curl -sS -m 3 "http://127.0.0.1:$CDP/json/version" | tr -d '\n ' | head -c 120)"
else
  check 1 "no CDP on 127.0.0.1:$CDP -- run scripts/launch_chrome_cdp.sh"
fi
if command -v browser-harness >/dev/null 2>&1; then
  export BU_CDP_URL="${BU_CDP_URL:-http://127.0.0.1:$CDP}"
  if timeout 90 browser-harness doctor --json >/dev/null 2>&1; then check 0 "browser-harness doctor ok"
  else check 1 "browser-harness doctor reported a problem (run: BU_CDP_URL=$BU_CDP_URL browser-harness doctor)"; fi
fi

step "audio capture source"
if command -v ffmpeg >/dev/null 2>&1; then
  DEVICES="$("$PY" -c 'from creator_audio_intel.recorder import ffmpeg_audio_devices; print("\n".join(ffmpeg_audio_devices()))' 2>/dev/null)"
  if [[ -n "$DEVICES" ]]; then
    echo "  devices:"; echo "$DEVICES" | sed 's/^/    - /'
    if echo "$DEVICES" | grep -qiE 'blackhole|monitor|null|loopback'; then
      check 0 "a loopback-style capture device is present"
    else
      check 1 "no loopback device: only microphones are available (run scripts/inspect_audio.sh)"
    fi
  else
    check 1 "no audio input device detected (run scripts/inspect_audio.sh)"
  fi
fi

step "database"
"$PY" -m creator_audio_intel.cli --db "$DB" doctor ${EXTRA[@]+"${EXTRA[@]}"} || FAILED=1

step "llm endpoint"
"$PY" - <<'PYEOF' || FAILED=1
import os
from creator_audio_intel.llm import resolve_config
cfg = resolve_config()
print(f"  base_url: {cfg['base_url']}")
print(f"  model:    {cfg['model']}")
print(f"  api_key:  {'set' if cfg['api_key'] else 'MISSING'}")
raise SystemExit(0 if cfg['api_key'] else 1)
PYEOF

step "fixture site (offline live-browser tier)"
if curl -sS -m 3 "http://127.0.0.1:${CAI_FIXTURE_PORT:-8099}/healthz" >/dev/null 2>&1; then
  check 0 "fixture site reachable"
else
  echo "  [note] fixture site not running (scripts/serve_fixture.py --port 8099)"
fi

echo
if [[ "$FAILED" == "1" ]]; then
  echo "DOCTOR: blocking issues found (see [FAIL] above)"
  exit 1
fi
echo "DOCTOR: no blocking issues detected"

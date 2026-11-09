#!/usr/bin/env bash
# Linux bootstrap: create the venv, install the package, and set up the virtual
# audio loopback that Chrome capture requires.
#
# Deliberately NOT done automatically (needs your decision, not mine):
#   * anything requiring sudo beyond package installation is printed, not run
#   * no service is enabled, no capture is scheduled, no data is deleted
#
# Usage: scripts/bootstrap_linux.sh [--no-audio] [--python PATH] [--help]
# Exit codes: 0 ready, 1 a step failed, 2 bad usage.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || exit 2

PYTHON="${CAI_PYTHON:-python3}"
SKIP_AUDIO=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python) PYTHON="$2"; shift 2 ;;
    --no-audio) SKIP_AUDIO=1; shift ;;
    --help|-h) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "this bootstrap targets Linux (found $(uname -s))."
  echo "macOS notes: use avfoundation + BlackHole; see docs/LINUX_AUDIO_SETUP.md."
  exit 2
fi

FAILED=0
step() { printf '\n== %s\n' "$1"; }
ok()   { echo "  [ok  ] $1"; }
bad()  { echo "  [FAIL] $1"; FAILED=1; }

step "python >= 3.11"
if "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)'; then
  ok "$("$PYTHON" --version)"
else
  bad "$("$PYTHON" --version) is too old; install python3.11+ (pyproject requires >=3.11)"
fi

step "system packages"
for tool in ffmpeg ffprobe; do
  command -v "$tool" >/dev/null 2>&1 && ok "$tool -> $(command -v "$tool")" || bad "$tool missing (apt install ffmpeg)"
done
for tool in node uv; do
  command -v "$tool" >/dev/null 2>&1 && ok "$tool -> $(command -v "$tool")" || echo "  [note] $tool not found"
done
if command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 \
   || command -v chromium-browser >/dev/null 2>&1; then
  ok "a Chrome/Chromium binary is present"
else
  bad "no Chrome/Chromium found (needed for browser-harness)"
fi

step "browser-harness"
if command -v browser-harness >/dev/null 2>&1; then
  ok "$(browser-harness --version 2>/dev/null || echo present)"
else
  echo "  installing with uv (this is the upstream-supported path)"
  if uv tool install --python 3.12 --upgrade --force browser-harness; then
    ok "browser-harness $(browser-harness --version)"
  else
    bad "browser-harness install failed"
  fi
fi

step "python virtualenv"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv && ok "venv created" || bad "venv creation failed"
fi
if [[ -x .venv/bin/pip ]]; then
  .venv/bin/pip install -q --upgrade pip
  if .venv/bin/pip install -q -e '.[asr,dev]'; then ok "package + extras installed"; else bad "pip install failed"; fi
else
  bad "no pip inside .venv"
fi

step "virtual audio sink (PulseAudio / PipeWire)"
if [[ "$SKIP_AUDIO" == "1" ]]; then
  echo "  skipped (--no-audio)"
elif ! command -v pactl >/dev/null 2>&1; then
  bad "pactl not found (apt install pulseaudio-utils or pipewire-pulse)"
elif pactl list short sources 2>/dev/null | grep -q 'cai_sink.monitor'; then
  ok "cai_sink.monitor already exists"
else
  echo "  loading module-null-sink (user-level, reversible with: pactl unload-module)"
  if pactl load-module module-null-sink sink_name=cai_sink \
        sink_properties=device.description=CAI_Audio >/dev/null 2>&1; then
    ok "cai_sink created"
  else
    bad "could not create cai_sink"
  fi
  cat <<'TXT'
  Make it persistent by appending to ~/.config/pulse/default.pa:

      load-module module-null-sink sink_name=cai_sink sink_properties=device.description=CAI_Audio
      set-default-sink cai_sink

  Then verify it end to end before any capture:

      scripts/inspect_audio.sh --backend pulse --device cai_sink.monitor

  A sink existing is NOT proof that Chrome routes into it: confirm the recorded
  WAV is non-silent and is the RIGHT audio, and keep notifications muted.
TXT
fi

step "ollama (no longer used)"
if command -v ollama >/dev/null 2>&1; then
  echo "  [note] ollama is installed but this project now extracts through the"
  echo "         OpenAI-compatible iagent endpoint (CAI_LLM_BASE_URL / CAI_LLM_API_KEY)."
fi

step "database"
mkdir -p data
if .venv/bin/python -m creator_audio_intel.cli init >/dev/null 2>&1; then ok "database initialised"; else bad "database init failed"; fi

echo
if [[ "$FAILED" == "1" ]]; then echo "BOOTSTRAP: completed with failures"; exit 1; fi
echo "BOOTSTRAP: ready"
echo
echo "next:"
echo "  1. scripts/doctor.sh"
echo "  2. scripts/launch_chrome_cdp.sh && export BU_CDP_URL=http://127.0.0.1:9222"
echo "  3. scripts/inspect_audio.sh --backend pulse --device cai_sink.monitor"
echo "  4. scripts/e2e_test.sh --tier offline"
echo "  5. scripts/e2e_test.sh --tier full-e2e --device cai_sink.monitor --backend pulse"

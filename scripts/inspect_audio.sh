#!/usr/bin/env bash
# Enumerate audio devices and validate that a capture source actually carries the
# audio you think it does. This is the check that decides whether Gate C can run.
#
# Usage:
#   scripts/inspect_audio.sh                       # enumerate only
#   scripts/inspect_audio.sh --probe '<device>'    # record 5s and analyse it
#   scripts/inspect_audio.sh --help
#
# Exit codes: 0 checks passed, 1 a check failed, 2 bad usage.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v "${CAI_PYTHON:-python3}")"

BACKEND="${CAI_AUDIO_BACKEND:-}"
DEVICE=""
PROBE=0
SECONDS_TO_RECORD="${CAI_AUDIO_PROBE_SECONDS:-5}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="$2"; shift 2 ;;
    --device) DEVICE="$2"; PROBE=1; shift 2 ;;
    --probe) DEVICE="$2"; PROBE=1; shift 2 ;;
    --seconds) SECONDS_TO_RECORD="$2"; shift 2 ;;
    --help|-h) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BACKEND" ]]; then
  case "$(uname -s)" in
    Darwin) BACKEND=avfoundation ;;
    Linux)  BACKEND=pulse ;;
    *)      BACKEND=pulse ;;
  esac
fi

echo "== platform"
uname -a
echo "ffmpeg: $(command -v ffmpeg || echo MISSING)"
"${FFMPEG:-ffmpeg}" -hide_banner -version 2>/dev/null | head -1

echo
echo "== backend: $BACKEND"

case "$BACKEND" in
  pulse)
    if command -v pactl >/dev/null 2>&1; then
      echo "-- pactl info"; pactl info 2>&1 | sed -n '1,12p'
      echo "-- sinks (outputs)"; pactl list short sinks 2>&1
      echo "-- sources (capture inputs)"; pactl list short sources 2>&1
      echo "-- sink-inputs (who is playing)"; pactl list short sink-inputs 2>&1
      echo
      echo "A capture source must be a MONITOR of the sink Chrome plays into."
      echo "Look for '<sink_name>.monitor' above. If there is none:"
      echo
      echo "    pactl load-module module-null-sink sink_name=cai_sink \\"
      echo "        sink_properties=device.description=CAI_Audio"
      echo "    # then route Chrome's output into cai_sink and use:"
      echo "    #   --backend pulse --device cai_sink.monitor"
      echo
      echo "Adding the null sink in ~/.config/pulse/default.pa survives restarts."
      echo "Note that '@DEFAULT_MONITOR@' follows the *default* sink, which is not"
      echo "necessarily the one Chrome uses."
    else
      echo "pactl not found: install pulseaudio-utils / pipewire-pulse."
    fi
    ;;
  avfoundation)
    echo "-- AVFoundation devices (ffmpeg prints these to stderr and exits 1)"
    "${FFMPEG:-ffmpeg}" -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
      | sed -n '/AVFoundation/,$p'
    echo
    echo "macOS has NO system-audio capture input by default. A virtual device is"
    echo "required; the built-in microphone cannot capture speaker output because"
    echo "the OS suppresses it (see GOTCHA-AUDIO-05):"
    echo
    echo "    brew install --cask blackhole-2ch     # needs your admin password"
    echo "    # then, in Audio MIDI Setup, create a Multi-Output Device containing"
    echo "    # both BlackHole 2ch and your speakers, and set it as the output."
    echo "    #   --backend avfoundation --device 'BlackHole 2ch'"
    echo
    echo "Use the device NAME, not its index: indices shift as Bluetooth devices"
    echo "connect and disconnect (GOTCHA-AUDIO-04)."
    ;;
  dshow)
    echo "Windows: use a tested virtual loopback input and pass --device 'audio=...'"
    ;;
  *)
    echo "unknown backend: $BACKEND" >&2
    exit 2
    ;;
esac

if [[ "$PROBE" == "0" ]]; then
  echo
  echo "device enumeration only; re-run with --device '<name>' to record and analyse"
  exit 0
fi

echo
echo "== probe: recording ${SECONDS_TO_RECORD}s from '$DEVICE' ($BACKEND)"
OUT="$(mktemp -t cai_audio_probe).wav"
echo "Play the target audio NOW (Chrome tab audible, system volume > 0)."
"${FFMPEG:-ffmpeg}" -hide_banner -loglevel warning -y \
  -f "$BACKEND" -i "$DEVICE" -vn -ac 1 -ar 16000 -c:a pcm_s16le \
  -t "$SECONDS_TO_RECORD" "$OUT"
RC=$?
if [[ $RC -ne 0 || ! -s "$OUT" ]]; then
  echo "FAIL: ffmpeg could not capture from '$DEVICE' (exit $RC)"
  echo "      wrong device name, device busy, or a permission prompt was denied"
  rm -f "$OUT"
  exit 1
fi

"$PY" - "$OUT" <<'PY'
import sys
from creator_audio_intel.recorder import analyze_wav
r = analyze_wav(sys.argv[1])
print(f"  file            {r['path']}")
print(f"  format          {r['sample_rate']} Hz, {r['channels']}ch, {r['sample_width_bytes']}B  ok={r['format_ok']}")
print(f"  duration        {r['duration_seconds']}s")
print(f"  rms / peak      {r['rms']} / {r['peak']} ({r['peak_dbfs']} dBFS)")
print(f"  silence ratio   {r['silence_ratio']}")
print(f"  clipped frac    {r['clipped_fraction']}")
print(f"  sha256          {r['sha256']}")
PY

echo
echo "-- verdict"
"$PY" - "$OUT" <<'PY'
import sys
from pathlib import Path
from creator_audio_intel.recorder import wav_quality
q = wav_quality(Path(sys.argv[1]))
print(f"  qc_ok={q.qc_ok} reason={q.qc_reason} rms={q.rms} peak={q.peak}")
if q.qc_ok:
    print('  PASS: this source produced a usable capture.')
    print('  Still verify by ear and by transcript that it is the RIGHT audio:')
    print('  .venv/bin/python scripts/asr_benchmark.py <wav>')
else:
    print('  FAIL: this source did not produce a usable capture.')
    print('  Fix the routing/volume before running any live capture.')
PY
RC=$?
if [[ "${CAI_KEEP_PROBE:-0}" != "1" ]]; then rm -f "$OUT"; else echo "kept $OUT"; fi
exit $RC

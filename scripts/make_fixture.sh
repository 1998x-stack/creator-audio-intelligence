#!/usr/bin/env bash
# Build deterministic local capture fixtures: real mp4s (video + Chinese speech
# audio) plus their ground-truth transcripts.
#
# These are *local* fixtures for exercising the real browser -> real ffmpeg ->
# real ASR -> real LLM path. They are NOT evidence of Douyin compatibility or of
# any third-party work; they exist so the pipeline can be verified reproducibly
# without depending on an external site or on permission to record someone
# else's content.
#
# Usage: scripts/make_fixture.sh [outdir]   (default: data/fixtures)
set -euo pipefail

OUTDIR="${1:-data/fixtures}"
VOICE="${FIXTURE_VOICE:-Tingting}"
TEXTS_DIR="$(cd "$(dirname "$0")" && pwd)/fixture_texts"
mkdir -p "$OUTDIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "make_fixture.sh uses macOS 'say'. On Linux install espeak-ng and set FIXTURE_TTS=espeak," >&2
  echo "or drop in any 16 kHz mono PCM16 Chinese speech WAV per text file." >&2
  exit 2
fi

names=()
for text_file in "$TEXTS_DIR"/*.txt; do
  name="$(basename "$text_file" .txt)"
  names+=("$name")
  echo "== [$name] synthesizing with voice '$VOICE'"
  say -v "$VOICE" -o "$OUTDIR/$name.raw.wav" --data-format=LEI16@16000 "$(cat "$text_file")"
  # `say` only honours --data-format for WAVE/CAF output; .aiff ignores it.
  ffmpeg -hide_banner -loglevel error -y -i "$OUTDIR/$name.raw.wav" \
    -ac 1 -ar 16000 -c:a pcm_s16le "$OUTDIR/$name.wav"
  rm -f "$OUTDIR/$name.raw.wav"

  dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUTDIR/$name.wav")"
  echo "   duration ${dur}s -> $OUTDIR/$name.mp4"

  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=640x360:rate=25:duration=${dur}" \
    -i "$OUTDIR/$name.wav" \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p -g 25 \
    -c:a aac -b:a 96k -shortest -movflags +faststart \
    "$OUTDIR/$name.mp4"
done

python3 - "$OUTDIR" "${names[@]}" <<'PY'
import json, sys, subprocess
from pathlib import Path
outdir = Path(sys.argv[1])
manifest = []
for name in sys.argv[2:]:
    mp4 = outdir / f'{name}.mp4'
    dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'csv=p=0', str(mp4)], capture_output=True, text=True).stdout.strip())
    manifest.append({'name': name, 'media': mp4.name, 'wav': f'{name}.wav',
                     'truth': Path('scripts/fixture_texts') .joinpath(name + '.txt').as_posix(),
                     'duration_seconds': round(dur, 2)})
(outdir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print('\nwrote', outdir / 'manifest.json')
for row in manifest:
    print(f"  {row['name']:<16} {row['duration_seconds']:>6.2f}s  {row['media']}")
PY

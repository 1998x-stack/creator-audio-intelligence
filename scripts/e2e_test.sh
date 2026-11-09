#!/usr/bin/env bash
# Automated test runner across explicit tiers. A pass in a lower tier is NEVER
# reported as a pass in a higher one.
#
# Tiers (cumulative unless --tier is given):
#   offline      unit + regression tests, no network, no devices
#   mock         offline plus mocked pipeline replay (same as offline here)
#   hardware     real FFmpeg capture from a real device + WAV QC
#   live-browser real Chrome via browser-harness against the LOCAL FIXTURE SITE
#   live-douyin  real douyin.com discovery  (network-blocked on most hosts)
#   full-e2e     live-browser capture -> ASR -> LLM -> SQLite -> verify
#
# Usage:
#   scripts/e2e_test.sh [--tier TIER] [--limit N] [--keep-db] [--help]
#
# Exit codes: 0 all selected tiers passed, 1 a tier failed, 2 bad usage,
#             3 a tier was skipped for a documented reason (reported, not hidden).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v "${CAI_PYTHON:-python3}")"
cd "$ROOT" || exit 2

TIER="offline"
LIMIT=1
KEEP_DB=0
DB="data/e2e.sqlite3"
FIXTURE_PORT="${CAI_FIXTURE_PORT:-8099}"
CDP_PORT="${CAI_CHROME_PORT:-9222}"
DEVICE="${CAI_AUDIO_DEVICE:-}"
BACKEND="${CAI_AUDIO_BACKEND:-}"
export BU_CDP_URL="${BU_CDP_URL:-http://127.0.0.1:$CDP_PORT}"
export BH_TAB_MARKER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier) TIER="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --db) DB="$2"; shift 2 ;;
    --keep-db) KEEP_DB=1; shift ;;
    --help|-h) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$BACKEND" ]]; then
  case "$(uname -s)" in Darwin) BACKEND=avfoundation ;; *) BACKEND=pulse ;; esac
fi

PASS=0; FAIL=0; SKIP=0
RESULTS=()
record() { RESULTS+=("$1|$2|$3"); case "$1" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; SKIP) SKIP=$((SKIP+1));; esac; }

run_tier() {
  local name="$1"; shift
  case "$name" in
    offline|mock|hardware|live-browser|live-douyin|full-e2e) ;;
    *) echo "unknown tier: $name" >&2; return 2 ;;
  esac
  printf '\n########## TIER: %s ##########\n' "$name"
  "$@"
}

tier_offline() {
  echo "-- compile"
  "$PY" -m compileall -q src scripts || { record FAIL offline "compile failed"; return 1; }
  echo "-- pytest"
  if "$PY" -m pytest -q; then record PASS offline "pytest suite"; else record FAIL offline "pytest failed"; return 1; fi
  echo "-- fixture availability"
  if [[ -f data/fixtures/manifest.json ]]; then
    record PASS offline "fixtures present"
  else
    echo "   generating fixtures (scripts/make_fixture.sh)"
    if ./scripts/make_fixture.sh >/dev/null; then record PASS offline "fixtures generated"; else record FAIL offline "fixture generation"; return 1; fi
  fi
  echo "-- sqlite verification on a fresh database"
  rm -f "$DB" "$DB-wal" "$DB-shm" "$DB.run.lock"
  "$PY" -m creator_audio_intel.cli --db "$DB" init >/dev/null || { record FAIL offline "db init"; return 1; }
  if "$PY" scripts/verify_sqlite.py "$DB" --quiet; then record PASS offline "sqlite verify"; else record FAIL offline "sqlite verify"; return 1; fi
  echo "-- real pipeline on a real WAV + real ASR + real LLM (no browser, no recording)"
  local first
  first="$("$PY" - <<'PYEOF'
import json, pathlib
d = pathlib.Path('data/fixtures')
m = json.loads((d / 'manifest.json').read_text())
print(d / m[0]['wav'])
PYEOF
)"
  "$PY" -m creator_audio_intel.cli --db "$DB" import-wav --platform fixture \
      --profile "http://127.0.0.1:$FIXTURE_PORT/user/local-e2e-creator" \
      --video-url "http://127.0.0.1:$FIXTURE_PORT/video/9000000000000000000" \
      --wav "$first" --rights-ok >/dev/null || { record FAIL offline "import-wav"; return 1; }
  if "$PY" -m creator_audio_intel.cli --db "$DB" transcribe --video-row-id 1; then
    record PASS offline "real whisper transcription"
  else
    record FAIL offline "real whisper transcription"; return 1
  fi
  echo "-- ASR benchmark against committed ground truth"
  if "$PY" scripts/asr_benchmark.py "$first" --truth scripts/fixture_texts/01_macro.txt --json data/fixtures/asr_benchmark.json | tee /tmp/cai_asr_bench.txt | grep -q 'CER ='; then
    record PASS offline "ASR benchmark ($(grep -oE 'CER = .*' /tmp/cai_asr_bench.txt | tail -1))"
  else
    record FAIL offline "ASR benchmark"
  fi
  if [[ -n "${CAI_SKIP_LLM:-}" ]]; then
    record SKIP offline "real LLM extraction (CAI_SKIP_LLM set)"
  else
    if "$PY" -m creator_audio_intel.cli --db "$DB" extract --video-row-id 1; then
      record PASS offline "real LLM extraction + evidence validation"
    else
      record FAIL offline "real LLM extraction"; return 1
    fi
  fi
  if "$PY" scripts/verify_sqlite.py "$DB" --backup-to "$DB.backup" --quiet; then
    record PASS offline "sqlite verify incl. backup/restore"
  else
    record FAIL offline "sqlite verify incl. backup/restore"; return 1
  fi
}

tier_hardware() {
  if [[ -z "$DEVICE" ]]; then
    echo "no --device given; set CAI_AUDIO_DEVICE to a validated loopback source."
    echo "Run scripts/inspect_audio.sh first. SKIPPING hardware tier."
    record SKIP hardware "no capture device provided"
    return 3
  fi
  echo "-- real 3s capture from $BACKEND '$DEVICE'"
  if ./scripts/inspect_audio.sh --backend "$BACKEND" --device "$DEVICE" --seconds 3; then
    record PASS hardware "real capture + QC"
  else
    record FAIL hardware "real capture + QC"; return 1
  fi
}

start_fixture() {
  if curl -sS -m 3 "http://127.0.0.1:$FIXTURE_PORT/healthz" >/dev/null 2>&1; then
    FIXTURE_STARTED=0; return 0
  fi
  "$PY" -u scripts/serve_fixture.py --port "$FIXTURE_PORT" --quiet >/tmp/cai_fixture_site.log 2>&1 &
  FIXTURE_PID=$!
  FIXTURE_STARTED=1
  for _ in $(seq 1 40); do
    curl -sS -m 2 "http://127.0.0.1:$FIXTURE_PORT/healthz" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "fixture site failed to start; see /tmp/cai_fixture_site.log" >&2
  return 1
}

stop_fixture() {
  [[ "${FIXTURE_STARTED:-0}" == "1" ]] && kill "${FIXTURE_PID:-0}" 2>/dev/null
  return 0
}

tier_live_browser() {
  if ! curl -sS -m 3 "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
    echo "no CDP on 127.0.0.1:$CDP_PORT; run scripts/launch_chrome_cdp.sh"
    record SKIP live-browser "no Chrome CDP endpoint"; return 3
  fi
  start_fixture || { record FAIL live-browser "fixture site did not start"; return 1; }
  echo "-- real browser discovery (positive)"
  local out
  out="$("$PY" -m creator_audio_intel.cli --db "$DB" discover --platform fixture \
        --profile "http://127.0.0.1:$FIXTURE_PORT/user/local-e2e-creator" --scroll-rounds 1 2>&1)"
  echo "$out"
  if grep -q '"visible_videos_found": 3' <<<"$out"; then
    record PASS live-browser "discovery found the 3 published works"
  else
    record FAIL live-browser "discovery count"; stop_fixture; return 1
  fi
  echo "-- real browser discovery (negative taxonomy)"
  if "$PY" - "$FIXTURE_PORT" <<'PYEOF'
import sys
from creator_audio_intel.browser import BrowserHarness, DiscoveryError
port = sys.argv[1]
expect = {'empty': 'EMPTY_PROFILE', 'loginwall': 'LOGIN_REQUIRED', 'broken': 'DOM_CHANGED'}
browser = BrowserHarness(timeout=90)
bad = []
for name, code in expect.items():
    try:
        browser.discover(f'http://127.0.0.1:{port}/user/{name}', 0, platform='fixture')
        bad.append(f'{name}: returned success')
    except DiscoveryError as exc:
        print(f'   {name} -> {exc.code}')
        if exc.code != code:
            bad.append(f'{name}: expected {code} got {exc.code}')
raise SystemExit(1 if bad else 0)
PYEOF
  then record PASS live-browser "negative taxonomy EMPTY_PROFILE/LOGIN_REQUIRED/DOM_CHANGED"
  else record FAIL live-browser "negative taxonomy"; stop_fixture; return 1; fi

  echo "-- real playback (record-free): pinned player, currentTime advances, real end"
  if "$PY" - "$FIXTURE_PORT" <<'PYEOF'
import sys, time
from creator_audio_intel.browser import BrowserHarness
port = sys.argv[1]
url = f'http://127.0.0.1:{port}/video/9000000000000000000'
b = BrowserHarness(timeout=120)
b.open(url)
prep = b.prepare_player()
if not prep.get('ok'):
    print(f'   prepare failed: {prep}'); raise SystemExit(1)
meta = b.wait_for_metadata(25)
if not meta.get('ok'):
    print(f'   metadata failed: {meta}'); raise SystemExit(1)
print(f"   duration={meta['duration']}s focused={prep.get('focused')}")
play = b.play()
if play.get('paused', True):
    print(f'   play failed: {play}'); raise SystemExit(1)
time.sleep(3)
st = b.inspect_player()
adv = float(st.get('currentTime') or 0)
print(f'   currentTime after 3s = {adv:.2f}s pinned={st.get("pinned")}')
if adv < 1.5 or not st.get('pinned'):
    print('   playback did not advance or the pin was lost'); raise SystemExit(1)
PYEOF
  then record PASS live-browser "real playback + pinning + progress"
  else record FAIL live-browser "real playback"; stop_fixture; return 1; fi
  stop_fixture
  return 0
}

tier_live_douyin() {
  echo "-- douyin reachability"
  if curl -sS -m 10 -o /dev/null "https://www.douyin.com/" 2>/dev/null; then
    echo "   reachable. Discovery requires an authorized creator homepage:"
    echo "   $PY -m creator_audio_intel.cli discover --profile 'https://www.douyin.com/user/<id>'"
    health=""
  else
    echo "   NOT reachable from this network (TCP accepted, then reset during the TLS"
    echo "   handshake -- see GOTCHA-ENV-01). Bypassing a network access control is"
    echo "   out of scope, so this tier is skipped rather than worked around."
    record SKIP live-douyin "douyin unreachable from this network (GOTCHA-ENV-01)"
    return 3
  fi
  if [[ -z "${CAI_DOUYIN_PROFILE:-}" ]]; then
    record SKIP live-douyin "set CAI_DOUYIN_PROFILE to an authorized creator homepage"
    return 3
  fi
  "$PY" -m creator_audio_intel.cli --db "$DB" discover \
      --profile "$CAI_DOUYIN_PROFILE" --scroll-rounds 2 \
    && record PASS live-douyin "discovery against douyin" \
    || { record FAIL live-douyin "discovery against douyin"; return 1; }
}

tier_full_e2e() {
  if ! curl -sS -m 3 "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1; then
    record SKIP full-e2e "no Chrome CDP endpoint"; return 3
  fi
  if [[ -z "$DEVICE" ]]; then
    echo "full-e2e needs a validated loopback capture device (see scripts/inspect_audio.sh)."
    record SKIP full-e2e "no capture device provided"
    return 3
  fi
  start_fixture || { record FAIL full-e2e "fixture site"; return 1; }
  rm -f "$DB" "$DB-wal" "$DB-shm" "$DB.run.lock"
  echo "-- capture -> ASR -> LLM -> SQLite for $LIMIT fixture video(s)"
  if "$PY" -m creator_audio_intel.cli --db "$DB" run --platform fixture \
        --profile "http://127.0.0.1:$FIXTURE_PORT/user/local-e2e-creator" \
        --backend "$BACKEND" --device "$DEVICE" --limit "$LIMIT" --scroll-rounds 1 \
        --rights-ok --llm-model "${CAI_LLM_MODEL:-iagent/standard}" | tee /tmp/cai_full_e2e.json; then
    if grep -q '"failed": \[\]' /tmp/cai_full_e2e.json && grep -q '"completed": '"$LIMIT" /tmp/cai_full_e2e.json; then
      record PASS full-e2e "capture+ASR+LLM completed $LIMIT video(s)"
    else
      record FAIL full-e2e "run reported failures"; stop_fixture; return 1
    fi
  else
    record FAIL full-e2e "run exited non-zero"; stop_fixture; return 1
  fi
  if "$PY" scripts/verify_sqlite.py "$DB" --backup-to "$DB.backup" --quiet; then
    record PASS full-e2e "sqlite verification"
  else
    record FAIL full-e2e "sqlite verification"; stop_fixture; return 1
  fi
  stop_fixture
}

case "$TIER" in
  offline)      run_tier offline tier_offline ;;
  mock)         run_tier mock tier_offline; echo "NOTE: 'mock' == offline here; nothing in this repo calls a mocked browser a live run." ;;
  hardware)     run_tier offline tier_offline && run_tier hardware tier_hardware ;;
  live-browser) run_tier offline tier_offline && run_tier live-browser tier_live_browser ;;
  live-douyin)  run_tier offline tier_offline && run_tier live-douyin tier_live_douyin ;;
  full-e2e)     run_tier offline tier_offline && run_tier hardware tier_hardware \
                  && run_tier live-browser tier_live_browser && run_tier full-e2e tier_full_e2e ;;
esac
RC=$?

stop_fixture

printf '\n########## SUMMARY (tier=%s) ##########\n' "$TIER"
for row in ${RESULTS[@]+"${RESULTS[@]}"}; do
  IFS='|' read -r status name detail <<<"$row"
  printf '%-5s %-14s %s\n' "$status" "$name" "$detail"
done
printf '\npass=%d fail=%d skip=%d\n' "$PASS" "$FAIL" "$SKIP"

if [[ "$KEEP_DB" != "1" && -f "$DB" ]]; then
  echo "database kept at $DB (pass --keep-db to suppress this notice; it is gitignored either way)"
fi

if [[ "$FAIL" -gt 0 ]]; then exit 1; fi
if [[ "$SKIP" -gt 0 && "$PASS" -eq 0 ]]; then exit 3; fi
if [[ "$RC" == "3" ]]; then exit 3; fi
exit 0

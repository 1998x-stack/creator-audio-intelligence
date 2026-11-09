#!/usr/bin/env bash
# Fast smoke test: is the install usable at all? Read-only with respect to the
# real database (uses a temporary one). No browser, no capture, no network beyond
# the configured LLM endpoint (which is only checked for configuration, not called).
#
# Usage: scripts/smoke_test.sh [--llm-call]
# Exit codes: 0 pass, 1 fail, 2 bad usage.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="$(command -v "${CAI_PYTHON:-python3}")"
cd "$ROOT" || exit 2

LLM_CALL=0
[[ "${1:-}" == "--llm-call" ]] && LLM_CALL=1

FAILED=0
step() { printf '\n== %s\n' "$1"; }
ok()   { echo "  [ok  ] $1"; }
bad()  { echo "  [FAIL] $1"; FAILED=1; }

step "import path"
"$PY" - <<'PYEOF' && ok "package imports and CLI is wired" || bad "package import"
from creator_audio_intel import cli, pipeline, browser, recorder, asr, llm, db, security
assert cli.parser() is not None
PYEOF

step "unit + regression tests"
if "$PY" -m pytest -q >/tmp/cai_smoke_pytest.txt 2>&1; then
  ok "$(tail -1 /tmp/cai_smoke_pytest.txt)"
else
  bad "pytest failed (see /tmp/cai_smoke_pytest.txt)"
fi

step "temporary database"
TMPDB="$(mktemp -t cai_smoke).sqlite3"; rm -f "$TMPDB"
"$PY" -m creator_audio_intel.cli --db "$TMPDB" init >/dev/null 2>&1 && ok "cai init" || bad "cai init"
"$PY" scripts/verify_sqlite.py "$TMPDB" --quiet >/dev/null 2>&1 && ok "sqlite verification" || bad "sqlite verification"
rm -f "$TMPDB" "$TMPDB-wal" "$TMPDB-shm"

step "configuration (no side effects)"
"$PY" - <<'PYEOF'
from creator_audio_intel.llm import resolve_config
cfg = resolve_config()
print(f"  llm:      {cfg['base_url']} model={cfg['model']} key={'set' if cfg['api_key'] else 'MISSING'}")
from creator_audio_intel.recorder import ffmpeg_audio_devices
devs = ffmpeg_audio_devices()
print(f"  audio:    {devs if devs else 'NO INPUT DEVICE'}")
loop = [d for d in devs if any(k in d.lower() for k in ('blackhole','monitor','null','loopback','cai_sink'))]
print(f"  loopback: {loop if loop else 'NONE (system audio cannot be captured yet)'}")
raise SystemExit(0 if cfg['api_key'] else 1)
PYEOF
[[ $? -eq 0 ]] && ok "LLM endpoint configured" || bad "LLM API key missing"

step "existing database (if present)"
if [[ -f data/creator_intel.sqlite3 ]]; then
  if "$PY" scripts/verify_sqlite.py data/creator_intel.sqlite3 --quiet >/dev/null 2>&1; then
    ok "data/creator_intel.sqlite3 verifies"
  else
    bad "data/creator_intel.sqlite3 failed verification"
  fi
else
  echo "  [note] data/creator_intel.sqlite3 not present yet"
fi

if [[ "$LLM_CALL" == "1" ]]; then
  step "live LLM call (explicitly requested)"
  if "$PY" -c 'from creator_audio_intel.llm import healthcheck; import json; print(json.dumps(healthcheck(), ensure_ascii=False))'; then
    ok "LLM endpoint answered"
  else
    bad "LLM endpoint did not answer"
  fi
fi

echo
if [[ "$FAILED" == "1" ]]; then echo "SMOKE: FAILED"; exit 1; fi
echo "SMOKE: PASSED"

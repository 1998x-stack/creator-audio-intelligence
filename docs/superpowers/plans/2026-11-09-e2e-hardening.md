# Creator Audio Intelligence — E2E Hardening Implementation Plan (macOS host, Linux target)

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the v0.2.0 prototype into a verified, resumable, evidence-linked pipeline on the
operator's host: real browser control, real FFmpeg capture, real Whisper small ASR, real
`iagent/standard` LLM extraction (replacing local Ollama), with SQLite integrity, recovery,
operational scripts, docs, and honest gate reporting.

**Architecture:** Keep the existing single-host, serial, SQLite-as-system-of-record design.
Changes are (a) provider swap Ollama → OpenAI-compatible `iagent/standard`, (b) browser adapter
hardening from real-Chrome observations, (c) explicit discovery error taxonomy, (d) operational
scripts + docs, (e) real-evidence verification for every claim.

**Tech Stack:** Python 3.12, SQLite (WAL), faster-whisper small (CTranslate2, CPU int8),
FFmpeg 9 loopback recorder, browser-harness 0.1.13 over CDP, `iagent/standard` OpenAI-compatible
chat completions with `json_schema` structured output.

**Spec:** `docs/PI_PROMPT.md` (operator master prompt) + `docs/PRD.md`.

## Global Constraints

- Host is **macOS 27.2 arm64 (Apple M5, 16 GB)** — NOT Linux. Linux-only gates must be reported
  as NOT RUN with the reason, never as passed.
- Do not bypass access controls: no login/CAPTCHA/DRM evasion, no private endpoints, no
  network-block circumvention.
- No synthetic WAV or mock browser result may be presented as proof of live Douyin E2E.
- LLM provider is `iagent/standard` via `IAUTO_BASE_URL` + `IAUTO_API_KEY`. No local Ollama.
- Never commit media, cookies, credentials, or DB files.
- Claims in docs require a command + observed output. Otherwise write `NOT RUN`.

## Verified environment facts (evidence: §Environment in docs/TEST_REPORT.md)

| Fact | Value |
|---|---|
| OS | Darwin 27.2.0 / arm64 / Apple M5 / 16 GB |
| Python | 3.12.15 (`/opt/homebrew/bin/python3.12`); system python3 is 3.9.6 (too old) |
| Chrome | 156.0.8078.81, dedicated profile, CDP on 127.0.0.1:9222 |
| browser-harness | 0.1.13 (uv tool) — connects, real DOM read OK |
| FFmpeg | 9.0.2, only `-f avfoundation` input; **no loopback/pulse device** |
| Whisper | faster-whisper small, CPU int8 (to be benchmarked) |
| LLM | `iagent/standard` @ https://iagent.iauto.com/v1 — OpenAI-compatible, `json_schema` OK |
| Douyin | **BLOCKED**: TCP 443 connects, then RST after TLS ClientHello (SNI filtering). HTTP/80, TLS1.2, direct-IP all fail. baidu.com works. |

## Task list

- [ ] **T1 — Baseline**: venv + `pip install -e '.[dev]'` + `pytest -q` (25 passed) + branch.
- [ ] **T2 — Browser adapter fixes** (from real Chrome evidence): player must be scrolled into
      view before the gesture click; wait for `loadedmetadata` before trusting `duration`;
      pin the player element for the whole record lifecycle; tag/label discovery errors.
      Files: `src/creator_audio_intel/browser.py`, `tests/test_browser.py`.
- [ ] **T3 — Discovery error taxonomy**: `EMPTY_PROFILE | DISCOVERY_FAILED | LOGIN_REQUIRED |
      ACCESS_DENIED | DOM_CHANGED | TIMEOUT` surfaced from `discover()` instead of silent `[]`.
- [ ] **T4 — LLM provider swap**: new `llm.py` client for OpenAI-compatible chat completions with
      `json_schema` response_format, retry/backoff, and the **unchanged** evidence validator.
      Config via env (`CAI_LLM_BASE_URL`/`CAI_LLM_API_KEY`/`CAI_LLM_MODEL`), defaults to iagent.
      Files: `src/creator_audio_intel/llm.py`, `cli.py`, `pipeline.py`, `tests/test_llm.py`.
- [ ] **T5 — Real LLM integration test** against live `iagent/standard`: valid extraction,
      hallucinated-quote rejection, empty-forecast, long-transcript chunk merge, retry on bad JSON.
- [ ] **T6 — Real ASR**: faster-whisper small on real Chinese speech; segment timestamps; CER
      measurement; offline re-run. Files: `docs/ASR_BENCHMARK.md`.
- [ ] **T7 — SQLite integrity + recovery suite**: integrity_check, FK enforcement, WAL, duplicate
      suppression, stage-wise resume, backup/restore, SIGKILL mid-write. `scripts/verify_sqlite.py`.
- [ ] **T8 — Recorder hardening**: orphan FFmpeg reaping, SIGTERM handling, real FFmpeg WAV
      produced and QC'd (real tone + real silence); macOS/Linux device guidance.
- [ ] **T9 — Operational scripts**: `bootstrap_linux.sh`, `doctor.sh`, `smoke_test.sh`,
      `e2e_test.sh`, `inspect_audio.sh`, `verify_sqlite.py`, with modes
      `offline|mock|hardware|live-browser|live-douyin|full-e2e` and explicit exit codes.
- [ ] **T10 — Service unit** (systemd --user + launchd) and `docs/LINUX_DEPLOYMENT.md`.
- [ ] **T11 — Docs**: rename `GOTCHAS.md` → `gotchas.md` (+ all references), `LINUX_ENVIRONMENT.md`,
      `LINUX_AUDIO_SETUP.md`, `ASR_BENCHMARK.md`, `TEST_REPORT.md`, update README/ARCHITECTURE/
      ROADMAP/IMPLEMENTATION_STATUS.
- [ ] **T12 — Git**: commits per stage, push branch, open PR, no merge to main.

## Blocked gates (must be reported, not faked)

| Gate | Status | Reason |
|---|---|---|
| C/D live Douyin capture | **BLOCKED** | Network resets TLS to `*.douyin.com`; bypassing is forbidden |
| C audio loopback (macOS) | **BLOCKED** | No virtual audio device installed; needs admin install of BlackHole |
| E systemd service | **NOT RUN** | macOS host; unit provided but not executed |

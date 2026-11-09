# Test report — 2026-11-09

Honest account of what was executed, on what, and what could not be executed. Items
that were not run say **NOT RUN** with the reason. Nothing in this document is
inferred from a mock.

## 0. Identity

| Field | Value |
|---|---|
| Commit SHA | `157766c` (branch `deploy/linux-e2e-hardening`, + this docs commit) |
| Date | 2026-11-09 |
| Host | macOS 27.2.0 (Darwin 27.2.0), arm64, Apple M5, 16 GB RAM, 658 GB free |
| Target platform | Linux (per specification) — see `docs/LINUX_ENVIRONMENT.md` |

## 1. Environment / dependency versions

| Component | Version / value | How verified |
|---|---|---|
| Python | 3.12.15 (`/opt/homebrew/bin/python3.12`) | `python -V` in `.venv` |
| system Python | 3.9.6 (unsupported, see GOTCHA-ENV-03) | `/usr/bin/python3 --version` |
| pytest | 9.2.0 | `pip show pytest` |
| faster-whisper | 1.2.1 | `import faster_whisper` |
| CTranslate2 | 4.8.5 | `pip list` |
| PyAV | 18.1.0 (pinned `<19`, see GOTCHA-ASR-01) | `pip show av` |
| zhconv | installed (dev extra, benchmark only) | `pip list` |
| FFmpeg | 9.0.2 (Homebrew), `avfoundation` only as audio input | `ffmpeg -version`, `-devices` |
| Chrome | 156.0.8078.81, dedicated profile `~/.cai/chrome-profile` | `GET /json/version` |
| browser-harness | 0.1.13 (uv tool) | `browser-harness --version` |
| LLM | `iagent/standard` at `https://iagent.iauto.com/v1` (OpenAI-compatible) | `POST /v1/chat/completions` |
| SQLite | 3.x bundled with Python; WAL, `foreign_keys=ON` | `PRAGMA` via `cai doctor` |

Ollama is **not** installed and is no longer used (GOTCHA-LLM-03).

## 2. Test commands and counts

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[asr,dev]'
python -m compileall -q src scripts
python -m pytest -q
```

| Run | Result |
|---|---|
| Baseline before any change | **25 passed** in 0.16 s |
| After hardening | **53 passed** in 0.19 s |
| `scripts/smoke_test.sh` | `SMOKE: PASSED` |
| `scripts/e2e_test.sh --tier offline` | **7 pass / 0 fail / 0 skip** |
| `scripts/e2e_test.sh --tier live-browser` | **10 pass / 0 fail / 0 skip** |
| `scripts/e2e_test.sh --tier hardware` | **SKIPPED** — no loopback capture device (GOTCHA-AUDIO-05) |
| `scripts/e2e_test.sh --tier live-douyin` | **SKIPPED** — douyin unreachable (GOTCHA-ENV-01); exit 3 |
| `scripts/e2e_test.sh --tier full-e2e` | **SKIPPED** — needs both of the above |

Test suite composition (53):

| File | Focus |
|---|---|
| `test_db.py` | SQLite CRUD, dedup, state transitions, crash recovery, attempts |
| `test_audio.py` | WAV QC, device argument validation |
| `test_browser.py` | CLI wrapper shape, discovery script content |
| `test_llm.py` | Evidence validator, forecast/kind rules |
| `test_pipeline.py` | Offline transcript → extraction |
| `test_run_pipeline.py` | Two-video serial idempotence (fakes, labelled) |
| `test_security.py` | URL and video-id validation |
| `test_enhancements.py` | WAV import idempotence, search, chunking, resume, lock, ASR segments |
| `test_regressions_browser.py` | GOTCHA-BROWSER-01/02/03/04/05/06 |
| `test_regressions_audio.py` | GOTCHA-AUDIO-01/02/03/04 |
| `test_regressions_llm.py` | GOTCHA-LLM-01/02/03, GOTCHA-SEC-02 |
| `test_regressions_db.py` | GOTCHA-SQLITE-01, backup/restore, platform isolation |

Pass / Fail / Skip: **53 passed, 0 failed, 0 skipped** in the unit suite. At the
script level, 17 checks passed and 3 tiers were skipped with recorded reasons.

## 3. Gate A — Environment

| Check | Status | Evidence |
|---|---|---|
| Python install/deps reproducible | **PASS** | `.venv` from `python3.12`, `pip install -e '.[asr,dev]'`, 53 tests pass |
| FFmpeg works | **PASS** | 9.0.2; captured real WAVs from a real device |
| Chrome + browser-harness connect | **PASS** | `BU_CDP_URL=http://127.0.0.1:9222 browser-harness doctor` → daemon alive, 1 connection |
| Virtual sink + monitor work | **NOT RUN** | macOS host has no loopback device; needs BlackHole (GOTCHA-AUDIO-05). Linux path documented, unexercised |
| Whisper small real inference | **PASS** | 48.5 s Mandarin decoded in 7.2–14.8 s CPU int8, CER 1.11 % |
| LLM returns structured JSON | **PASS** | Live `iagent/standard` call, `json_schema` strict, 6 claims |

## 4. Gate B — Offline pipeline (no browser, no recording)

`scripts/e2e_test.sh --tier offline` — all real components.

| Step | Result |
|---|---|
| `cai init` on a fresh DB | PASS |
| `verify_sqlite.py` (integrity, FK, WAL, chain, dupes, state) | PASS |
| `import-wav` real 48.5 s Mandarin WAV (QC: rms 4083.57, duration 48517 ms) | PASS |
| Real Whisper `small` transcription → 2 timestamped segments, `model_version=1.2.1+peaknorm-v1` | PASS |
| CER benchmark vs committed ground truth | **CER = 2/180 = 1.11 %**; numbers 2025/4.9/3.3/5.8/7.2 all correct; 5/5 domain terms correct |
| Real `iagent/standard` extraction (prompt v2) | PASS — `llm_runs.provider=iagent.iauto.com` |
| Evidence validation | PASS — **6/6 quotes verbatim** in their cited segments |
| `verify_sqlite.py --backup-to` incl. backup/restore row counts | PASS |

Chain recorded in SQLite: `discovered → recording → captured → transcribed → analyzed`,
with `source_video_url`, `platform_video_id`, `capture_timestamp`, `wav_path`,
`wav_sha256`, `audio_duration`, `quality_status` and row ids all present and traceable
via `cai evidence --video-row-id N`.

### ASR quality (human-reviewed)

Reference and hypothesis differ by exactly 2 characters after script folding:
an inserted `在` (`房地产方面` → `在房地产方面`) and `企稳` → `起稳`.

Numbers: expected `2025, 4.9, 3.3, 5.8, 7.2` — all present and correct.
Terms: `国内生产总值`, `社会消费品零售总额`, `工业增加值`, `商品房销售面积`, `投资建议` — all correct.

Caveats: the fixture is synthesised speech (macOS `say`, voice `Tingting`), which is
easier than natural noisy speech. This is **not** a benchmark of Douyin audio quality.
See `docs/ASR_BENCHMARK.md`.

### LLM extraction (human spot-check)

| kind | statement (truncated) | verbatim quote | forecast |
|---|---|---|---|
| fact | 国家统计局最新数据显示，前三季度国内生产总值同比增长4.9%。 | yes | — |
| fact | 社会消费品零售总额增长3.3%。 | yes | — |
| fact | 工业增加值增长5.8%。 | yes | — |
| fact | 全国商品房销售面积同比下降7.2%。 | yes | — |
| forecast | 四季度消费增速可能会继续回落。 | yes | metric=消费增速 direction=继续回落 horizon=四季度 cond=∅ |
| forecast | 如果政策继续加码，明年一季度房地产可能出现企稳迹象。 | yes | metric=房地产 direction=起稳 horizon=明年一季度 cond=政策继续加码 |

Hedged wording was preserved (`可能` stayed conditional; the condition is recorded
rather than dropped). No claim was upgraded to certainty. `verification_status`
remains `not_evaluated` — this is extraction, not a verified economic prediction.

## 5. Gate C — Real browser

Executed against a real Chrome 156.0.8078.81 on a real local site
(`scripts/serve_fixture.py`, platform `fixture`) because Douyin is unreachable
(GOTCHA-ENV-01). The Douyin DOM adapter itself remains **unverified**.

| Requirement | Status | Evidence |
|---|---|---|
| Open a real video page | **PASS** | `open()` returned the requested URL with `readyState=complete` |
| Verify the correct video | **PASS** | requested/actual video id compared; mismatch fails closed |
| Detect playback start | **PASS** | `play()` → `paused:false` after activation; `NotAllowedError` handled |
| Record real playback audio | **BLOCKED** | no loopback device; see GOTCHA-AUDIO-05/06 |
| Detect playback end | **PASS** | real `ended` at `currentTime=48.51675` = exact `duration` |
| WAV non-silent, not truncated | **PARTIAL** | FFmpeg capture from a real device produced a valid 16 kHz mono PCM16 WAV that passed format/level QC; the *content* was the room, not the video (GOTCHA-AUDIO-06) |
| Correct storage in SQLite | **PASS** | `captures` row with path, sha256, duration, rms, QC reason; `cai evidence` reproduces the chain |
| Creator identity verified | **NOT RUN** | requires Douyin homepage proof |
| Discovery over a real DOM | **PASS** | 3 works found on the fixture homepage via real anchors |
| Discovery never returns an unlabelled zero | **PASS** | `EMPTY_PROFILE` / `LOGIN_REQUIRED` / `DOM_CHANGED` classified live; `ACCESS_DENIED` on an unreachable host |
| Player pinned for the whole record | **PASS** | `pinned:true` on every poll of a full 48 s recording |
| No contamination from another video | **PASS (local)** | `wait_for_end` fails closed on navigation or pin loss; exercised on the fixture site, not on Douyin |
| Audio capture error handling | **PASS** | `Whisper returned no speech segments` on the bad capture instead of a silent success |

Real playback trace (fixture video, 48.51675 s):

```
prepare : {"ok": true, "inViewport": true, "focused": true, "activated_tab": true}
metadata: {"ok": true, "duration": 48.51675, "readyState": 4}
play    : {"ok": true, "paused": false, "currentTime": 0.28}
+2s     : paused=false currentTime=2.41 pinned=true
+4s     : paused=false currentTime=4.49 pinned=true
+6s     : paused=false currentTime=6.57 pinned=true
...
+48s    : ended=true currentTime=48.51675 pinned=true
```

## 6. Gate D — Serial operation

**NOT RUN.** A three-video serial run requires both a working capture route
(GOTCHA-AUDIO-05) and a reachable target (GOTCHA-ENV-01). What was verified instead:

| Requirement | Status | Evidence |
|---|---|---|
| No duplicate records on re-discovery | **PASS** | two `discover` runs → `3 videos, 1 creator` |
| No duplicate record on re-capture | **PASS** | `test_serial_two_videos_no_duplicate` + `run_profile` second pass `queued: 0` |
| Successful capture is not re-recorded | **PASS** | `resume_downstream` retries only `captured`/`transcribed` stages |
| Stage-wise recovery after ASR/LLM failure | **PASS** | `test_resume_stages_without_second_capture` asserts exactly one ASR call across two resumes |
| Interruption cannot report false success | **PASS** | a QC-failing capture never advances the row to `captured`; verified by `verify_sqlite.py` ("no video advanced without a QC-passed capture") |
| Rows left in `recording` are recoverable | **PASS** | `reset_interrupted()` reclassifies them as `failed` |
| Orphan FFmpeg after a crash | **PASS (unit)** | `reap_orphans` kills only verified ffmpeg pids |
| SIGTERM stops FFmpeg and marks failure | **PASS (unit)** | `terminate_as_exception` raises inside `capture_video`'s cleanup |
| Each result traceable one by one | **PASS** | `cai evidence --video-row-id N` |
| Three-video serial pilot | **NOT RUN** | blocked as above |

## 7. Gate E — Operations

| Requirement | Status | Evidence |
|---|---|---|
| Deployment reproducible from a clean env | **PASS (macOS)** / **NOT RUN (Linux)** | `bootstrap_linux.sh` written and syntax-checked; executed only in `--no-audio`-ish form here. x86/ARM Linux not available |
| Service start/stop/restart | **NOT RUN** | no `systemd` on this host; unit provided untested |
| Logs and status inspectable | **PASS** | `cai doctor`, `cai stats`, `cai evidence`, `scripts/doctor.sh`, `.ffmpeg.log`, `job_events` |
| Gotchas document complete | **PASS** | `docs/gotchas.md`: 20 observed entries + 10 explicitly-not-reproduced risks |
| Automated tests repeatable | **PASS** | `pytest` 53/53 twice; `--tier offline` and `--tier live-browser` re-run clean |
| Every real blocker recorded | **PASS** | GOTCHA-ENV-01, ENV-02, AUDIO-05, AUDIO-06 |

## 8. Security checks

| Check | Result |
|---|---|
| CDP bound to loopback only | PASS — `lsof` shows `127.0.0.1:9222`; launcher refuses non-loopback |
| Dedicated Chrome profile, no personal cookies | PASS — `~/.cai/chrome-profile`, gitignored |
| No credential in `argv` | PASS — no `--llm-api-key` flag exists |
| API key never in an error message | PASS — regression test |
| LLM endpoint policy | PASS — HTTPS or loopback; embedded credentials rejected |
| No media / DB / cookies / `.env` committed | PASS — `git check-ignore` on every artifact; `git ls-files` grep for sensitive suffixes is empty |
| No access-control bypass attempted | PASS — Douyin block recorded as `ACCESS_DENIED`, not worked around |
| `--rights-ok` still required for any capture/import | PASS — enforced in CLI and `import_wav` |

## 9. Known unresolved issues

| ID | Severity | Issue | Impact |
|---|---|---|---|
| GOTCHA-ENV-01 | P0 | douyin.com unreachable (TLS reset) | Douyin-specific gates C/D cannot run here |
| GOTCHA-AUDIO-05 | P0 | macOS suppresses playback audio on a speaker→mic route | No validated capture route on this host; needs BlackHole (admin) or Linux |
| GOTCHA-ENV-02 | P1 | Host is macOS, not Linux | Linux systemd/Xvfb/Pulse gates are `NOT RUN` |
| GOTCHA-AUDIO-06 | P1 | QC cannot prove *which* audio was captured | Requires human listening / transcript confirmation before trusting a capture |
| GOTCHA-ASR-02 | P2 | ASR may emit Traditional Chinese | Literal search for a Simplified term can miss; script folding is benchmark-only |
| POTENTIAL-03 | P2 | WAL growth in a long-running service | Not exercised; no long-running service started |

## 10. Release readiness

**Not release-ready for unattended Douyin ingestion.** Two independent P0 blockers
remain outside the control of the code: the target site is unreachable from this
network, and no validated system-audio capture route exists on this host.

What *is* demonstrably working end to end, on real components and real files: URL
validation → SQLite → WAV import/QC → real Whisper small transcription with timestamps
and measured CER → real `iagent/standard` structured extraction with 6/6 verbatim
evidence quotes → integrity-checked, backup-verified SQLite with stage-wise resume,
duplicate suppression and orphan/SIGTERM recovery. Real browser control, discovery
(with a failure taxonomy instead of silent zeros), player pinning and playback
start/end detection are all verified against a real Chrome.

Next step to unblock: install a virtual audio device (macOS: BlackHole; Linux: the
`cai_sink` null sink) or run on the Linux target, then re-run
`scripts/e2e_test.sh --tier full-e2e --device <source> --backend <backend>`.

# Implementation status and release gates

**2026-11-09, branch `deploy/linux-e2e-hardening` (base v0.2.0).**
Legend: `IMPLEMENTED_AND_VERIFIED` / `IMPLEMENTED_NOT_VERIFIED` / `PARTIALLY_IMPLEMENTED` /
`BLOCKED` / `NOT_IMPLEMENTED`. Verification evidence is in `docs/TEST_REPORT.md`; every
`BLOCKED` item has an entry in `docs/gotchas.md`.

## Component status

| Component | Status | Evidence / blocker |
|---|---|---|
| SQLite schema, dedupe, transitions, retry, events | IMPLEMENTED_AND_VERIFIED | 53 tests; `verify_sqlite.py` incl. backup/restore |
| Stage-wise resume (ASR/LLM) without re-recording | IMPLEMENTED_AND_VERIFIED | real run + regression test asserting one ASR call across two resumes |
| Source URL + video id validation | IMPLEMENTED_AND_VERIFIED | Douyin HTTPS-only; separate `fixture` platform, tested |
| FFmpeg recorder (real device → WAV → QC) | IMPLEMENTED_AND_VERIFIED | real 16 kHz mono PCM16 capture, format/level/silence/clip QC |
| Capture **routing** to Chrome's audio | **BLOCKED** | macOS suppresses playback on the speaker→mic route (GOTCHA-AUDIO-05); needs BlackHole or Linux `cai_sink` |
| Orphan FFmpeg reaping, SIGTERM safety | IMPLEMENTED_AND_VERIFIED | regression tests; conservative pid verification |
| Whisper `small` ASR with timestamps + provenance | IMPLEMENTED_AND_VERIFIED | real decode on real audio; CER 1.11 %; `model_version` records preprocessing |
| Quiet-capture normalisation | IMPLEMENTED_AND_VERIFIED | +20 dB recovers decodable audio; raw WAV untouched |
| LLM extraction via `iagent/standard` | IMPLEMENTED_AND_VERIFIED | live endpoint, `json_schema` strict, 6/6 verbatim quotes |
| Evidence validator (contiguous segments + verbatim quote) | IMPLEMENTED_AND_VERIFIED | runtime + DB-level re-verification in `verify_sqlite.py` |
| Long-transcript chunking + merge de-dup | IMPLEMENTED_NOT_VERIFIED | offline unit tests only; no long real transcript available |
| CJK substring search | IMPLEMENTED_NOT_VERIFIED | offline unit test; Traditional/Simplified folding not applied to stored text (GOTCHA-ASR-02) |
| Single-host run lock | IMPLEMENTED_AND_VERIFIED | unit test; never contended on a real run |
| browser-harness discovery (real DOM) | IMPLEMENTED_AND_VERIFIED | 3/3 works found on a real local homepage through real Chrome |
| Discovery failure taxonomy | IMPLEMENTED_AND_VERIFIED | EMPTY_PROFILE / LOGIN_REQUIRED / DOM_CHANGED live; ACCESS_DENIED on error page |
| Player pinning, gesture play, real end detection | IMPLEMENTED_AND_VERIFIED | 48.5 s playback with `pinned:true`, real `ended` at exact `duration` |
| Douyin-specific DOM adapter | **BLOCKED** | site unreachable from this network (GOTCHA-ENV-01); bypassing is out of scope |
| Creator identity verified from the page | NOT_IMPLEMENTED | requires Douyin access; ownership is not asserted |
| Complete historical works traversal | NOT_IMPLEMENTED | only visibly rendered links are read |
| Anti-contamination (autoplay/recommendation audio) | PARTIALLY_IMPLEMENTED | fails closed on navigation, player swap, stall, replay, pin loss; unverified against real Douyin |
| systemd / launchd service | IMPLEMENTED_NOT_VERIFIED | unit files provided, **NOT RUN** (macOS host) |
| Xvfb / headless-Chrome audio | NOT_IMPLEMENTED | not assumed to work; untested |
| Automated test tiers | IMPLEMENTED_AND_VERIFIED | `offline` 7/7, `live-browser` 10/10; `hardware`, `live-douyin`, `full-e2e` skip with reasons |

## Release gates

| Gate | State |
|---|---|
| A — Environment | **PASSED** except the virtual audio sink (BLOCKED) |
| B — Offline pipeline | **PASSED** — real Whisper + real LLM + verified SQLite, 6/6 verbatim quotes |
| C — Real browser | **PARTIAL** — discovery/playback/storage verified; the audio hop is BLOCKED |
| D — Serial operation | **NOT RUN** — needs C's audio hop and a reachable target |
| E — Operations | **PARTIAL** — scripts verified; service units NOT RUN |

## Recommended next steps, in order

1. Provide a capture route: Linux `cai_sink.monitor`, or BlackHole on macOS.
2. Re-run `scripts/e2e_test.sh --tier full-e2e --backend <b> --device <d>` for one video.
3. Then a three-video serial pilot with human listening checks, per `docs/PRD.md`.
4. Only then consider a timer-driven service and larger ingestion.

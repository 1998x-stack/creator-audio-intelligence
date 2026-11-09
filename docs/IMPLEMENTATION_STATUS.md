# Implementation status and release gates

**2026-11-09, v0.2.0 — usable offline/local stages, experimental Douyin browser adapter.**

| Component | Implemented | Verified |
|---|---|---|
| SQLite schema, dedupe, events, retry transitions | Yes | Offline pytest |
| Source URL validation | Yes | Offline pytest |
| Douyin visible-link discovery through browser-harness | Experimental | Mock only, real site not verified |
| HTML5 video preparation/play/end polling | Experimental | Mock only, real site not verified |
| FFmpeg Pulse/AVFoundation/DShow loopback | Yes, device-dependent | WAV parser/synthetic QC only |
| WAV import without browser | Yes | Offline pytest |
| Whisper small ASR with timestamps | Yes | Source code checks only; model not run locally |
| Ollama schema-constrained claim extraction | Yes | Validator tests; real model not run |
| Long transcript chunking and evidence guard | Yes | Offline pytest |
| Resume ASR/LLM without new capture | Yes | Offline pytest |
| CJK substring search | Yes | Offline pytest |
| Single-worker host lock | Yes | Offline pytest |
| Creator identity verified from Douyin page | No | Requires real site adapter |
| Complete historical works traversal | No | Cannot claim completeness |
| Full real Chrome → WAV → Whisper → Ollama end-to-end | No | Requires user machine, auth and permissions |

## Production release gates

1. One authorized 2–5 minute real video: verify correct author, actual URL, playable WAV content, start/end integrity, and timing. Review result manually.
2. Three sequential authorized videos: check exact one WAV per work, idempotent rerun, clean failure upon navigation/stall/autoplay, and resumption.
3. Manual ASR transcript accuracy audit: CER plus numeric entities and names against hand-reviewed text, model performance on the intended hardware.
4. LLM extraction review: false quote rejection and factual grounding, at least 20 sampled video claims; confirm forecast conditions/time qualifiers.
5. Negative tests on access-denied, CAPTCHA, DRM and private media; fail closed.

GitHub Actions only checks compile and offline tests; it has no user Chrome, loopback device, Whisper weights or Ollama model.

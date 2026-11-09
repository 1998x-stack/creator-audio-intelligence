# Incremental implementation plan

Status legend: DONE CODE / TESTED OFFLINE / REQUIRES LOCAL VALIDATION / FUTURE.

1. **Foundation**: Python package, CLI, SQLite schema, security validators, docs. DONE CODE, TESTED OFFLINE.
2. **Homepage discovery**: browser-harness visible `a[href]` scan and scroll. DONE EXPERIMENTAL; REQUIRES REAL DOUYIN TEST. Support click-only cards and virtualized grids later.
3. **Single video audio**: independent FFmpeg loopback WAV and HTML5 media player heuristics. DONE EXPERIMENTAL; REQUIRES REAL CHROME, AUDIO ROUTING AND AUTHORIZED SAMPLE.
4. **Batch state**: one-video sequential loop, attempts, failed restart. DONE PROTOTYPE; process-lock/orphan-cleanup still FUTURE.
5. **Local ASR**: faster-whisper `small`, default CPU INT8, segment timestamps and model provenance. DONE CODE; requires model download, quality benchmark and audio sample.
6. **Local LLM**: Ollama structured JSON + exact quote evidence validation + SQLite claims. DONE CODE; requires installed local model, manual extraction quality benchmark.
7. **One-video e2e test**: real authorized Douyin clip, audio source check, ASR CER/numeric audit and LLM evidence. REQUIRES LOCAL VALIDATION.
8. **Three-video pilot**: rerun without duplicates, stall/crash recovery, source attribution and correct stop. REQUIRES LOCAL VALIDATION.
9. **Site-specific hardening**: creator UID proof, works-tab interactions, scrolling pagination, side effects, protected content, autoplay, recommendation audio, moderation and consent. FUTURE.
10. **Search & UI**: Chinese search strategy, Streamlit optional, human review of predictions and timestamps. FUTURE.

Do not expand features before Gate B from PRD is passed. A robust audio-only capture is more valuable than dozens of unreliable modules.

## Implementation additions in v0.2
- [x] Offline authorized WAV import and source-linked SQLite ingestion.
- [x] Stage-specific resume after ASR/LLM failure, without re-recording.
- [x] Segment-based long transcript LLM chunking with evidence validation.
- [x] Single-host exclusive recording lock and literal SQLite transcript search.
- [ ] One-video browser/loopback validation on the operator's Mac/Linux machine.
- [ ] Site-specific verified creator identity; virtualized works list traversal and full discovery coverage.
- [ ] Exact audio-to-video sync and contamination checks with manually reviewed samples.
- [ ] Real local Whisper small and Ollama model accuracy/latency benchmark.

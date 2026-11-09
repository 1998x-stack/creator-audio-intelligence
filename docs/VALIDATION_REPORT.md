# Local validation report — 2026-11-09

Performed in this ChatGPT isolated Python container, **not** on the user's authenticated Chrome or computer.

- Python source compilation: pass (`python -m compileall -q src`).
- Offline pytest suite: **24 passed**. Covers SQLite CRUD/dedup/crash-state recovery, direct quote evidence validator, local URL restrictions, WAV QC, browser-harness CLI mocked interaction, 2-video sequential idempotence and fake ASR+LLM data persistence.
- Editable package install: pass (`pip install --no-build-isolation -e .`).
- CLI init/stats/doctor: pass. `doctor` reports installed executables, not functional browser/audio configuration.
- FFmpeg generated a 3s synthetic test tone with 16kHz mono PCM16 and wav_quality marked QC pass. This checks WAV parsing only; it does **not** verify system-loopback recording, playback of real videos, or copyrighted content.

## Not validated here

- Access to Douyin or a target creator account; creator identity, homepage enumeration completeness and HTML5 player selectors.
- A true Chrome browser-harness connection and user-approved permissions.
- macOS BlackHole or Linux PipeWire/PulseAudio audio routing, clipping, contamination, buffering and real end event.
- First-time multilingual Whisper small weights downloading, CPU/GPU speed or accuracy; no real speech recognition run.
- Ollama's actual selected local model, response latency, schema adherence and Chinese economic data extraction quality.
- Three real video sequential pilot and orphan-FFmpeg/OS-crash cleanup.

**Release gating:** Keep bulk ingestion disabled until one authorized real video and then a three-video controlled pilot pass manual source/audio/ASR checks. Current browser heuristics are experimental.

- Additional verified offline tests: WAV import idempotence and permission gate; substring search with literal wildcard handling; ASR retry without re-recording; long transcript chunk boundaries; noncontiguous LLM citation rejection; local single-writer lock.

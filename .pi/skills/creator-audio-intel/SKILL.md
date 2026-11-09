---
name: creator-audio-intel
summary: Run and improve a local-first Douyin video audio -> Whisper small -> Ollama -> SQLite pipeline.
---

Read README.md and docs/{PRD,ARCHITECTURE,ROADMAP,GOTCHAS,PI_PROMPT}.md before modifying code.

Operate one Chrome tab and one FFmpeg loopback recorder at a time. Browser-harness records screenshots/actions, not audio. Never treat `--rights-ok` as permission established by the agent; the operator must confirm. Start with 1 authorized public video and inspect WAV/ASR evidence before batch processing. Do not bypass login, DRM, CAPTCHA or other platform controls. Record real gotchas and results; do not invent success, source dates, creator identity, or complete historical coverage.

First commands: `python -m pip install -e '.[dev]' && pytest -q`, then `cai doctor`. Follow docs/PI_PROMPT.md for the staged real Chrome test. Do not let Pi and a separate browser-harness agent mutate the same Chrome tab concurrently.

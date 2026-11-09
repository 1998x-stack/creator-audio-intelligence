---
name: creator-audio-intel
summary: Run and improve a local-first creator video audio → Whisper small → iagent LLM → SQLite pipeline.
---

Read `README.md`, then `docs/{PRD,ARCHITECTURE,IMPLEMENTATION_STATUS,TEST_REPORT,gotchas}.md`,
before changing anything. `docs/gotchas.md` lists every fault found on real hardware,
with root causes and the command that verifies each fix — start there when something
already looks familiar.

## Non-negotiables

* One Chrome tab and one FFmpeg recorder at a time. browser-harness holds a **single
  attached tab per daemon**; parallel invocations interleave and misattribute results.
* browser-harness `start_recording()` records screenshots and actions, **not audio**.
  Audio comes from a separate FFmpeg process reading an OS **loopback** input. A
  microphone cannot capture system audio.
* Never treat `--rights-ok` as permission established by you; the operator asserts it.
  Stop at login walls, CAPTCHA, DRM, or uncertain rights.
* Never treat "QC passed" as "the right audio was recorded". Confirm by ear and by
  transcript. Verify the system output volume is above zero first.
* Never report a mock/browser-fixture result as a live Douyin result. The `fixture`
  platform exists precisely so the two cannot be confused.

## Environment

Extraction uses the OpenAI-compatible **`iagent/standard`** endpoint
(`CAI_LLM_BASE_URL` / `CAI_LLM_API_KEY` / `CAI_LLM_MODEL`, falling back to
`IAUTO_BASE_URL` / `IAUTO_API_KEY` / `IAUTO_MODEL`). The key is read from the
environment only — there is deliberately no `--llm-api-key` flag. Local Ollama is no
longer used.

Python **3.11+** is required; the macOS/Debian system `python3` (3.9) is too old.
`av` must stay `<19` (faster-whisper 1.2.x is incompatible with PyAV 19).

## First commands

```bash
/opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate   # or python3.12
pip install -e '.[asr,dev]'
scripts/launch_chrome_cdp.sh && export BU_CDP_URL=http://127.0.0.1:9222
scripts/doctor.sh                 # exits non-zero on a blocking gap
python -m pytest -q
scripts/e2e_test.sh --tier offline
```

Use the tiered runner rather than ad-hoc commands: `offline | mock | hardware |
live-browser | live-douyin | full-e2e`. It prints PASS/FAIL/SKIP per check and exits
`3` when a tier was skipped for a documented reason; a pass in a lower tier is never a
pass in a higher one.

## Adding a fix

Follow `docs/gotchas.md`: observe → reproduce → root cause → fix → regression test →
update the entry → commit. Do not add an entry for something that was not observed;
speculative risks belong in the "Potential / Not Reproduced" table. Every entry needs
a verification command that now passes.

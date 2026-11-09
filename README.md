# Creator Audio Intelligence (CAI)

A **standalone** local-first prototype: Douyin creator homepage → visible video links → one video at a time via browser-harness → FFmpeg loopback WAV → **local multilingual Whisper small** → local Ollama evidence-linked economic claims → SQLite.

**Status (2026-11): locally tested MVP code; live Douyin DOM, protected login, audio routing and end-to-end recording have NOT been verified on a user's Chrome.** The browser adapter uses visible-link/player heuristics and can return zero videos or fail when the site structure changes. A public video is not automatic permission to record/store/reproduce; obtain the necessary rights before using `--rights-ok`.

## Quick start

Python 3.11+, FFmpeg, Chrome, [browser-harness](https://github.com/browser-use/browser-harness), audio loopback device (Linux Pulse/PipeWire monitor or macOS BlackHole), optional local [Ollama](https://docs.ollama.com/) with an installed model.

```bash
# Ubuntu example: install ffmpeg and configure your Pulse/PipeWire monitor source first
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[asr,dev]'
# browser-harness: install separately with uv, connect to Chrome (read upstream install.md)
cai init
cai doctor
pytest -q
```

Preview visible video links without recording:

```bash
cai discover --profile 'https://www.douyin.com/user/<verified_creator_id>' --scroll-rounds 3
cai stats
```

**Experimental** authorized capture (Linux with a previously validated loopback source):

```bash
cai run \
  --profile 'https://www.douyin.com/user/<verified_creator_id>' \
  --backend pulse --device '@DEFAULT_MONITOR@' \
  --limit 3 --rights-ok --llm-model 'YOUR_INSTALLED_OLLAMA_MODEL'
```

macOS: `--backend avfoundation --device ':BlackHole 2ch'` (verify actual input device and route Chrome audio to it). If you want to validate recording without LLM, omit `--llm-model`; with no local Whisper dependencies, add `--skip-asr` for the initial capture pilot. The first Whisper small load may download weights; after caching, ASR runs locally. No remote transcription is used.

### Offline WAV ingestion (fallback when live site automation fails)

For a WAV you are permitted to process, first normalize it to mono PCM16 at 16 kHz:

```bash
ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le sample.wav
cai import-wav \
  --profile 'https://www.douyin.com/user/<verified_creator_id>' \
  --video-url 'https://www.douyin.com/video/<verified_video_id>' \
  --wav sample.wav --rights-ok
cai resume --llm-model 'YOUR_INSTALLED_OLLAMA_MODEL'
```

This fallback is fully implemented and works without a Douyin browser session. It does not prove the file comes from the asserted video: check the source yourself.

### Stage-by-stage and query commands

```bash
cai transcribe --video-row-id 1 --device cpu --compute-type int8
cai extract --video-row-id 1 --llm-model 'YOUR_INSTALLED_OLLAMA_MODEL'
cai resume --llm-model 'YOUR_INSTALLED_OLLAMA_MODEL'
cai search '房地产'
cai stats
```

`resume` retries ASR/LLM for previously captured videos without re-recording; persistent lock prevents concurrent CAI recording sessions on one host. Long transcripts are extracted in bounded chunks with grounded segment references.

### Important boundaries

- browser-harness `start_recording()` records **screenshots and action traces, not audio**. FFmpeg is a separate program recording from the OS input. Use a dedicated virtual output/input audio route, disable notifications and avoid unrelated app sounds.
- This repository does not download media via private site endpoints, bypass login, CAPTCHA or DRM, or evade access controls. Unsupported pages are reported as failures.
- Only one foreground/active recording at a time. Site autoplay, focus-gated playback, muted/silent routes and player DOM changes may break live capture. A successful CLI invocation is not proof of full historical coverage.
- Raw WAV files/transcripts live in ignored `data/` and should not be committed to public Git or redistributed without permission. A creator may have deleted, private or unavailable videos.
- SQLite WAL is suitable for one local writer and multiple readers, **not a network-mounted database or a parallel multi-agent writer cluster**.

## Documentation

- [Architecture and code categories](docs/ARCHITECTURE.md)
- [Product requirements and acceptance gates](docs/PRD.md)
- [Implementation verification and limitations](docs/IMPLEMENTATION_STATUS.md)
- [Audio and browser gotchas](docs/GOTCHAS.md)
- [Pi Agent prompt](docs/PI_PROMPT.md)
- [MVP implementation roadmap](docs/ROADMAP.md)
- [Schema and examples](schemas/extraction.schema.json)

## Status and coverage

Run `cai stats` for known per-video statuses. Discovery counts include **only links visibly observed by the browser on that run**, not proof of every historic work. Post-ASR and LLM processing can be run independently to recover after partial failures. A basic SQLite LIKE search supports small datasets; there is no benchmarked Chinese full-text search index yet.

## Example SQLite queries

See [docs/QUERIES.md](docs/QUERIES.md) for forecast extraction, transcript evidence and collection progress queries.

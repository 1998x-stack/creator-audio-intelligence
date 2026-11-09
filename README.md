# Creator Audio Intelligence (CAI)

A **standalone** local-first prototype: Douyin creator homepage → visible video links → one video at a time via browser-harness → FFmpeg loopback WAV → **local multilingual Whisper small** → local Ollama evidence-linked economic claims → SQLite.

**Status (2026-11): hardened and verified on real hardware as far as this host allows.** Real Chrome control, real FFmpeg capture, real Whisper `small` and real `iagent/standard` LLM extraction are verified end to end against a local fixture site and real audio files (see [docs/TEST_REPORT.md](docs/TEST_REPORT.md)). **Two blockers remain, both environmental:** `douyin.com` is unreachable from this network (TLS reset), and there is no validated system-audio capture route on this macOS host. Douyin DOM discovery, the three-video pilot and the systemd service are therefore **NOT RUN**. A public video is not automatic permission to record/store/reproduce; obtain the necessary rights before using `--rights-ok`.

## Quick start

Python 3.11+ (**not** the 3.9.6 shipped by macOS/Debian), FFmpeg, Chrome/Chromium, [browser-harness](https://github.com/browser-use/browser-harness), and an audio **loopback** device (Linux Pulse/PipeWire monitor, or macOS BlackHole). A microphone cannot capture system audio.

The extraction stage uses the OpenAI-compatible `iagent/standard` endpoint — **local Ollama is no longer used**. Configure `CAI_LLM_BASE_URL` / `CAI_LLM_API_KEY` / `CAI_LLM_MODEL` (falling back to `IAUTO_BASE_URL` / `IAUTO_API_KEY` / `IAUTO_MODEL`); the key is read from the environment only, never from a command-line flag.

```bash
# macOS/Homebrew example. Use an explicit 3.11+ interpreter.
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[asr,dev]'
uv tool install --python 3.12 --upgrade --force browser-harness

scripts/launch_chrome_cdp.sh                 # dedicated profile, CDP on 127.0.0.1 only
export BU_CDP_URL=http://127.0.0.1:9222
scripts/doctor.sh                            # readiness gate; exits non-zero on a gap
pytest -q                                    # 53 tests
```

On Linux, `scripts/bootstrap_linux.sh` does the same and creates the `cai_sink` loopback sink.

Preview visible video links without recording:

```bash
cai discover --profile 'https://www.douyin.com/user/<verified_creator_id>' --scroll-rounds 3
cai stats
```

Authorized capture, only after a validated loopback source exists:

```bash
cai run \
  --profile 'https://www.douyin.com/user/<verified_creator_id>' \
  --backend pulse --device 'cai_sink.monitor' \
  --limit 3 --rights-ok --llm-model iagent/standard
```

macOS: `--backend avfoundation --device 'BlackHole 2ch'` — use the device **name**, not
an index, because AVFoundation indices shift as Bluetooth devices connect and
disconnect. Validate the route first with `scripts/inspect_audio.sh --device '<name>'`;
it records a few seconds and refuses to call an unusable capture a success. Prefer the
explicit sink name over `@DEFAULT_MONITOR@`, which follows the *default* sink rather
than the one Chrome uses. The first Whisper `small` load downloads weights; afterwards
ASR runs fully locally. No remote transcription is used.

To exercise the whole chain without any external site or third-party content, use the
local fixture site (platform `fixture`, kept separate from `douyin` in SQLite):

```bash
scripts/make_fixture.sh
scripts/serve_fixture.py --port 8099 &
scripts/e2e_test.sh --tier live-browser
scripts/e2e_test.sh --tier offline          # real Whisper + real LLM + verified SQLite
```

`scripts/e2e_test.sh --tier` accepts `offline | mock | hardware | live-browser |
live-douyin | full-e2e`, prints a PASS/FAIL/SKIP row per check, and exits `3` when a
tier was skipped for a documented reason. A pass in a lower tier is never reported as
a pass in a higher one.

### Offline WAV ingestion (fallback when live site automation fails)

For a WAV you are permitted to process, first normalize it to mono PCM16 at 16 kHz:

```bash
ffmpeg -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le sample.wav
cai import-wav \
  --profile 'https://www.douyin.com/user/<verified_creator_id>' \
  --video-url 'https://www.douyin.com/video/<verified_video_id>' \
  --wav sample.wav --rights-ok
cai resume --llm-model iagent/standard
```

This fallback is fully implemented and works without a Douyin browser session. It does not prove the file comes from the asserted video: check the source yourself.

### Stage-by-stage and query commands

```bash
cai transcribe --video-row-id 1 --device cpu --compute-type int8
cai extract --video-row-id 1 --llm-model iagent/standard
cai resume --llm-model iagent/standard
cai evidence --video-row-id 1          # full provenance chain for one video
cai search '房地产'
cai stats
scripts/verify_sqlite.py data/creator_intel.sqlite3 --backup-to /tmp/cai-backup.sqlite3
```

`resume` retries ASR/LLM for previously captured videos without re-recording; persistent lock prevents concurrent CAI recording sessions on one host. Long transcripts are extracted in bounded chunks with grounded segment references.

### Important boundaries

- browser-harness `start_recording()` records **screenshots and action traces, not audio**. FFmpeg is a separate program reading an OS input. Use a dedicated virtual sink, disable notifications and avoid unrelated app sounds. Set the system output volume above zero: Chrome playing silently while the microphone records the room still passes audio QC, which is how a "successful" capture of the wrong audio happens.
- browser-harness keeps **one attached tab per daemon**. Concurrent invocations interleave. All work here is strictly serial.
- CDP has **no authentication**. `scripts/launch_chrome_cdp.sh` binds `127.0.0.1` only and refuses a non-loopback endpoint. Never expose the debugging port, the SQLite file or an admin endpoint off-host.
- This repository does not download media via private site endpoints, bypass login, CAPTCHA or DRM, or evade access controls. Unsupported pages are reported as failures.
- Only one foreground/active recording at a time. Site autoplay, focus-gated playback, muted/silent routes and player DOM changes may break live capture. A successful CLI invocation is not proof of full historical coverage.
- Raw WAV files/transcripts live in ignored `data/` and should not be committed to public Git or redistributed without permission. A creator may have deleted, private or unavailable videos.
- SQLite WAL is suitable for one local writer and multiple readers, **not a network-mounted database or a parallel multi-agent writer cluster**.

## Documentation

- [Architecture and code categories](docs/ARCHITECTURE.md)
- [Product requirements and acceptance gates](docs/PRD.md)
- [Implementation verification and limitations](docs/IMPLEMENTATION_STATUS.md)
- [Gotchas: every real fault, root cause and verification](docs/gotchas.md)
- [Test report: what was executed and what was NOT RUN](docs/TEST_REPORT.md)
- [Environment audit](docs/LINUX_ENVIRONMENT.md)
- [Audio capture setup](docs/LINUX_AUDIO_SETUP.md)
- [ASR benchmark](docs/ASR_BENCHMARK.md)
- [Deployment (incl. untested systemd unit)](docs/LINUX_DEPLOYMENT.md)
- [Pi Agent prompt](docs/PI_PROMPT.md)
- [MVP implementation roadmap](docs/ROADMAP.md)
- [Schema and examples](schemas/extraction.schema.json)

## Status and coverage

Run `cai stats` for known per-video statuses. Discovery counts include **only links visibly observed by the browser on that run**, not proof of every historic work. Post-ASR and LLM processing can be run independently to recover after partial failures. A basic SQLite LIKE search supports small datasets; there is no benchmarked Chinese full-text search index yet.

## Example SQLite queries

See [docs/QUERIES.md](docs/QUERIES.md) for forecast extraction, transcript evidence and collection progress queries.

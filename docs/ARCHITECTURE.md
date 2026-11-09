# Architecture / code categories

## Why this project is separate from economics-video-db

This is a self-contained single-machine Python + SQLite + Whisper small product. It should work without external cloud ASR, PostgreSQL, Redis, Kubernetes or a web front-end. Extensions such as Xigua/Bilibili can be added later by implementing independent site adapters. Creator-neutral domain types avoid hardcoding Wen Yifei into the schema.

## Source tree

```
creator-audio-intelligence/
  src/creator_audio_intel/
    cli.py           # user commands, explicit rights consent
    browser.py       # browser-harness CLI, Douyin link discovery and player adapter (EXPERIMENTAL)
    security.py      # URL allowlist and ID validation
    recorder.py      # FFmpeg Pulse/AVFoundation/DShow loopback, WAV QC
    asr.py           # local faster-whisper multilingual small
    llm.py           # structured Ollama JSON extraction + evidence validation
    db.py            # SQLite migrations / provenance / statuses
    pipeline.py      # process orchestration, sequential one-video capture
    models.py        # typed data contracts
  tests/             # offline unit and fake-API integration tests
  docs/              # PRD, roadmap, browser/audio gotchas, Pi prompt
  schemas/           # versioned LLM schema documentation
  .pi/skills/        # Pi agent on-disk skill
  data/              # ignored: media, transcript files, database
```

## Runtime data path

```
Creator URL
  -> verified page (browser-harness)
  -> visible video links and video IDs (not complete guaranteed)
  -> SQLite creators / videos
  -> one queued video, BrowserHarness.open -> prepare -> FFmpegLoopback.start
  -> BrowserHarness.play -> currentTime/ended checks -> FFmpegLoopback.stop
  -> wav_quality (length/RMS check) -> SQLite captures + job_events
  -> faster-whisper small -> SQLite transcripts/segments
  -> OpenAI-compatible chat-completions (`iagent/standard`) with a strict JSON schema
  -> evidence check (contiguous real segment ids + verbatim quote) -> SQLite llm_runs/claims

A capture that fails QC is stored with its reason and never advances the video, and an
empty transcript raises rather than being stored, so a "successful" row always has
verifiable backing.
```

## SQLite tables

- `creators(id, platform, profile_url, display_name, platform_user_id)`
- `videos(id, creator_id, platform_video_id, source_url, published_at, status, attempts, last_error)`
- `captures(id, video_id, wav_path, wav_sha256, duration_ms, rms, qc_ok)`
- `transcripts(id, capture_id, model_name, model_version, language, transcript_text)`
- `transcript_segments(id, transcript_id, segment_id, start_ms, end_ms, text)`
- `llm_runs(id, transcript_id, provider, model, prompt_version, summary, raw_json)`
- `claims(id, llm_run_id, video_id, kind, statement, evidence_quote, evidence_segment_ids_json, metric, direction, horizon, conditions)`
- `job_events(id, video_id, old_state, new_state, detail, happened_at)`

Use `PRAGMA foreign_keys=ON`, `journal_mode=WAL`, a single local writer and bounded retries. WAV remains outside the database; only paths, hashes and metadata go into SQLite. Keep all raw ASR segments and LLM outputs for audit. Future schema migration should add a schema_version table and Alembic equivalent; v0.1 uses CREATE IF NOT EXISTS only.

## State machine and idempotency

`discovered -> recording -> captured -> transcribed -> analyzed`

Video identity is `(platform, platform_video_id)`. Two platforms exist: `douyin`
(production, HTTPS + `douyin.com` only) and `fixture` (the local verification site,
loopback HTTP allowed). A fixture result therefore cannot be mistaken for, or collide
with, a Douyin result; `db.stats_by_platform()` reports the split.

`recording -> failed` on errors; after process crash, startup reclassifies stale `recording` as failed. Bounded retry: max 3 attempts. Missing video link, unconfirmed account, no HTML5 player, login/CAPTCHA, stall, failed FFmpeg, silence or audio duration mismatch must be logged as failure rather than success. Real process supervision / orphan reaping are not yet implemented, so use one CLI session only. Restart may require operator to stop an orphan FFmpeg process.

Unique video identity: `(platform,platform_video_id)`, **not title**. Source links and publication date are distinct from capture timestamp. For later cross-platform adaptation, deduplicate using audio fingerprint and source provenance, never by title alone.

## Media capture and timing

The system loopback source must contain *only* the target video audio. Start FFmpeg before playing, and stop when the page's selected video element reports `ended`. Detect wrong video navigation, seek-back/replay, stalled `currentTime`, excessive duration, and recorder errors. A short start/stop silence is expected. `wav_quality` checks format, length and RMS; it does not prove speech source correctness or exact completeness. Future QC must include sample playback comparisons, ASR language plausibility, clipping and music/noise detection.

## Agent boundaries

Pi can call the CLI through its Bash tool; optional pi-browser-harness provides browser tools but is a distinct third-party extension. Do not drive the same Chrome tab concurrently with two controllers: browser-harness
holds exactly one attached tab per daemon, so parallel invocations interleave. In an operational deployment, prefer Pi for discovery/error recovery and deterministic Python for recording/ASR/SQLite. Do not allow LLM to directly write SQL or transition state. No background job scheduler is implemented.

## Extraction contract

Only five claim kinds: fact, causal, opinion, recommendation, forecast. LLM must provide at least one referenced transcript segment and an exact quote in those segments. Unknown forecast horizons stay blank; no automatic correctness score. Publication date is optional and absent until verified; without it, relative forecast dates cannot be reliably resolved. Every extraction stores provider/model/prompt version and raw model response.

## Search and product later

MVP uses SQL filtering and indexed status/kind queries. SQLite FTS5 unicode61 tokenization isn't ideal for Chinese segmentation; evaluate Chinese tokenization or trigram search on real corpus before adding search UI. Possible UX: profiles, video list, precise transcript evidence playback, timeline and forecast review dashboard. Never equate an LLM's extracted prediction with verified economic fact.

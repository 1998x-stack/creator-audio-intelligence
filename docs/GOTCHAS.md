# Gotchas and debugging guide

## Browser
- `browser-harness start_recording()` is screenshot/action-trace recording, not audio.
- Browser-harness attaches to real Chrome via CDP and can see its profile. Enable `chrome://inspect/#remote-debugging`, approve access only for intended profile and use the upstream install guide.
- DOM parser currently reads visible `<a href=".../video/ID">` links; Douyin may render cards without anchors or virtualize older content. Zero results ≠ zero videos. No private API fallback.
- Browser tab is often opened in background; media playback may be throttled or require user gesture/foreground. If it fails, report blocked, don't invent an audio file.
- HTML5 media element may be absent, nested in a frame, or there may be multiple videos. Current largest-visible-player heuristic needs live validation.
- Do not close/navigate browser while recorder is active; autoplay recommendations may contaminate audio. Mark replay/seek interruption as failure.
- One local browser-harness daemon has one mutable attached tab; never run multiple agents in parallel on it.

## Audio
- FFmpeg needs a capture **input** that actually receives the browser sound. macOS: BlackHole + multi-output route; Linux: Pulse/PipeWire monitor; Windows: tested virtual loopback or DShow input.
- `@DEFAULT_MONITOR@` works only when Pulse-compatible input exists and routes relevant playback. A WAV file can be valid but silent.
- The recorder checks 16kHz PCM mono, duration, approximate RMS. RMS alone cannot detect the wrong person's audio or music-only noise.
- If FFmpeg crashes, inspect `data/audio/<video_id>/*.ffmpeg.log`; a crash can leave partial/orphan processes. Clean up before rerunning.
- A hung process is not guaranteed to be killed after power loss; Phase 2 should use a persistent process/lease monitor.

## ASR / LLM
- `small` (multilingual) is not `small.en`; the first call may download model weights and requires available network/cached weights.
- Chinese financial terms, percentages, dates, company names and mixed language often need human numeric/proper noun review. A fluent transcript is not necessarily accurate.
- Whisper segments are a generator; code materializes them before saving.
- Local Ollama model must be pulled separately and currently use structured output support. Very long transcripts cause an explicit error until chunking is implemented.
- The LLM evidence validator rejects quotes not in chosen transcript segments; this catches many hallucinations, not all incorrect interpretations.
- Forecast correctness requires future authoritative datasets and a reproducible definition; MVP does not score forecasts.

## SQLite
- WAL still allows only one writer at a time; no SQLite on network shares and no multi-agent parallel capture writes.
- CREATE IF NOT EXISTS in MVP is not a migration framework. Upgrade carefully once real user data exists.
- Unique (platform,video_id) prevents duplicate records but does not prove creator ownership; add account identity checks before a large run.
- Successful capture with failed ASR leaves the video captured; run `cai transcribe` explicitly rather than attempting a new recording.

## Copy-rights and user safety
- Do not retain copyrighted media/transcripts in public git. Public content may still be protected.
- `--rights-ok` is an explicit operator assertion, not a license detector. Stop on login wall, private pages, CAPTCHA/DRM or unverified rights.
- Audio loopback may record notifications, meeting audio or private speech. Use isolated routing and verify content locally.

# Pi Agent / Codex prompt for local build and validation

Use this as a prompt to a local agent with terminal and a dedicated Chrome profile (do not claim tests have passed before running them):

```text
You are developing the independent Creator Audio Intelligence project.
Read README.md, docs/PRD.md, docs/ARCHITECTURE.md, docs/ROADMAP.md and docs/GOTCHAS.md.

Goal: input a user-confirmed Douyin creator homepage URL, discover public works,
process ONE authorized video at a time, record audio through a dedicated
FFmpeg loopback input, transcribe it locally with multilingual faster-whisper
small, extract sourced facts/opinions/forecasts through local Ollama, store
all artifacts/provenance in SQLite, and resume from failures without duplicates.

Requirements:
1. Install the project and run pytest first. State exact results.
2. Inspect browser-harness documentation and the actual user's Chrome connection;
   do not invent Douyin selectors, private URLs, login bypasses or media download APIs.
3. Begin with a single authorized short video. Verify audio routing and that
   real WAV matches source and duration; test silent/failed capture paths.
4. Measure Whisper numeric and proper noun errors against manually reviewed speech.
5. Inspect every model-produced claim's original segment and quote; reject unsupported claims.
6. Test 3 videos sequentially with duplicate skip, autoplay contamination handling,
   stall/timeout and SIGINT crash recovery. Never mark an unverified capture complete.
7. Before proceeding to an unattended run, add persistent process locking, orphan
   FFmpeg cleanup, creator account verification, works-tab/virtualized list
   discovery and bounded checkpoints. Current browser selectors are experimental.
8. Keep raw media/transcripts/cookies outside public Git; respect access rights,
   platform limits and stop at login/CAPTCHA/DRM or uncertain authorization.
9. Record each real issue and precise reproduction/repair in docs/GOTCHAS.md.
10. Report implemented/tested/unverified scope accurately, with exact commands.

Do not start bulk ingest until the single-video and three-video acceptance gates pass.
```

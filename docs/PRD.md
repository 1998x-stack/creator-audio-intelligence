# PRD: Creator Audio Intelligence MVP

## User story

As a research analyst, I paste a verified public Douyin creator homepage URL, authorize permitted processing, and get a resumable local database of visible videos, recordings, timestamped transcripts, and sourced economic insights.

## Scope / goals

G1. Discover visible video URLs and deduplicate by platform video ID.
G2. Process one video at a time and save an audio file with checksum and duration.
G3. Run local Whisper multilingual small, retaining segments and ASR model version.
G4. Extract relevant claims, quotations and conditional forecasts with evidence validation.
G5. Persist everything in SQLite and support failed-job resume and stage re-run.

## Explicit non-goals for MVP

- Completeness guarantee for deleted/private/hidden historic works.
- Bypassing platform access restrictions or copyright protections.
- Public redistribution of full transcripts.
- Live accurate financial forecast scoring, trading advice or investment signals.
- Multi-machine parallel processing, dashboards, automated account authentication.

## Core workflow

1. User enters `--profile` and permission flag. Validate that it's a Douyin creator homepage.
2. Browser-harness opens page and discovers visible video cards/links via site adapter. Store URL, ID, any reliable metadata.
3. SQLite queues unseen videos; worker processes up to N with one browser and one FFmpeg loopback at a time.
4. Worker opens video, checks finite duration, pauses/seeks, starts recorder, plays, checks `currentTime`, awaits real end, then stops recorder.
5. Check WAV duration/format/energy; store `captures` with SHA-256. Preserve failure and retry context.
6. Whisper small creates raw transcript/segments; don't let LLM silently change what was said.
7. Local Ollama extracts claims and forecasts with evidence ID and exact quote, validates, writes to SQLite.

## KPIs / acceptance criteria

### Gate A: offline code sanity
- Unit tests for SQLite uniqueness and state transitions, URL validation, WAV quality and evidence checking pass.
- Mock end-to-end works without an internet connection or proprietary media.

### Gate B: 1 authorized real video (still pending)
- Creator identity verified by operator; video URL and ID match.
- WAV audibly matches that video, has plausible duration, RMS QC passes.
- Whisper small transcript stored with time-coded segments and corrected numbers reviewed.
- LLM quotes map back to actual transcript substrings.

### Gate C: 3-video sequential pilot (still pending)
- Three distinct videos recorded and processed; rerun skips successfully captured videos.
- Simulated FFmpeg failure, browser stall and login wall all fail closed; interrupted jobs resume.
- Manual checks detect irrelevant audio/recommendations/autoplay contamination.

### Gate D: history ingest
- Count discovered, accessible, recorded, transcribed, analyzed and missing/blocked videos separately.
- Never report "all videos" unless original creator's inventory or other independent complete index supports it.

## Quality thresholds (targets, not observed facts)

- 100% extracted claims have valid referenced segments and exact source quote.
- Zero silently overridden successful capture on a rerun.
- No claim of ASR numeric accuracy until measured using reviewed samples.
- Single active recorder and tab throughout a run.

## Privacy and rights

Recording, storing and processing third-party audiovisual works may require permission. The project defaults to no recording without `--rights-ok`. Media, transcript and browsing traces are local-only and ignored by Git. Use a dedicated Chrome profile and virtual audio route to avoid recording other people's private content.

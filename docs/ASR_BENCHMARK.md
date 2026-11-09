# ASR benchmark — faster-whisper `small`, CPU INT8

Measured on this machine with real audio and the real model. Reproduce with
`python scripts/asr_benchmark.py <wav> --truth <txt> --json out.json`, or
`scripts/e2e_test.sh --tier offline`.

## Configuration

| Setting | Value |
|---|---|
| Model | `small` (multilingual — **not** `small.en`) |
| Runtime | faster-whisper 1.2.1 / CTranslate2 4.8.5 |
| Device | `cpu`, `compute_type=int8` (no GPU present) |
| Decode options | `language='zh'`, `beam_size=5`, `vad_filter=True`, `condition_on_previous_text=False` |
| Preprocessing | bounded in-memory peak normalisation, target −3 dBFS, ≤ +20 dB (`peaknorm-v1`) |

The preprocessing version and any applied gain are stored in
`transcripts.model_version`, e.g. `1.2.1+peaknorm-v1+gain20.0dB`, so a stored
transcript can always be traced to the decoder input rule that produced it. The
stored WAV is never modified.

## Corpus

Three synthesised Mandarin clips (macOS `say`, voice `Tingting`), 48.5 s / 51.9 s /
52.3 s, built by `scripts/make_fixture.sh` from the text files in
`scripts/fixture_texts/`. Content is financial commentary chosen to stress the things
that actually break: percentages, years, quarters, domain terms, company/entity names
and hedged forecasts.

**These are synthesis, not Douyin audio.** Synthesised speech is cleaner than real
recorded speech, so these numbers are an upper bound and must not be quoted as an
expectation for real Douyin captures.

## Result — clip `01_macro` (48.5 s)

| Metric | Value |
|---|---|
| Segments returned | 2 |
| Decode wall-clock | 7.2 s (VAD on) |
| Realtime factor | ≈ 0.15 |
| **CER** | **2 / 180 = 1.11 %** |

The two edits, reviewed by hand:

1. insertion: `房地产方面` → `在房地产方面`
2. substitution: `企稳迹象` → `起稳迹象` (企 → 起, homophone)

Numbers — expected `2025, 4.9, 3.3, 5.8, 7.2`, all present and correct (5/5).
Domain terms — `国内生产总值`, `社会消费品零售总额`, `工业增加值`, `商品房销售面积`,
`投资建议`, all present and correct (5/5).

## Measurement methodology matters more than the number

The first measurement of this same clip was **30.61 %**, and almost all of it was
measurement error, not recognition error:

| Cause | Effect | Handling |
|---|---|---|
| Whisper emitted Traditional Chinese (`我們`, `數據`, `生產總值`) | every such character counted as an error; 3 domain terms reported "missing" | fold both sides to Simplified (`zhconv`); the benchmark *also* reports how many terms matched only after folding, so the issue stays visible |
| Spoken numerals rewritten (`百分之四点九` → `4.9%`, `二零二五` → `2025`) | numeric spot check reported `[MISS]` on correct audio | normalise `百分之X点Y` → `X.Y`, `百分之X` → `X`, digit runs ≥ 2 chars → digits |

After correct normalisation: **1.11 %**.

Two normalisation traps are now pinned in code comments, because both silently
corrupted the metric while it was being built:

* a blanket `零 → 0` mapping destroys **零售** ("retail", literally 零 + 售);
* a blanket `点 → .` mapping destroys 观点 / 重点.

An off-by-one in the digit table (including `〇` without mapping it) shifted every
digit, turning `二零二五` into `3036`.

## Known limitations

* **Multiplicative numerals** (`百分之三十`) are not converted; the benchmark reports
  them as a limitation rather than as recognition errors.
* **Script folding is benchmark-only.** The stored transcript keeps the raw ASR text,
  because the specification requires the original to be preserved and the LLM must
  quote it verbatim. Consequence: a literal search for a Simplified term will not
  match a Traditional transcript (`db.search` is a literal substring match).
* **Synthesised corpus.** No real Douyin audio was transcribable here because no
  capture route exists on this host (GOTCHA-AUDIO-05) and the site is unreachable
  (GOTCHA-ENV-01).
* **CER is not the whole story.** Numbers and abbreviations matter more than the mean
  character error rate for this product, which is why they are reported separately.
* **`small` is a floor, not a target.** If domain accuracy proves insufficient, the
  next step is a larger model (or a domain-adapted one) with the same benchmark, not
  a lower CER target.

## A real failure worth recording

A genuine 48 s loopback capture peaked at −19.5 dBFS and decoded to hallucinated
repetition (`字幕by索兰娅`) with VAD returning zero segments. Bounded normalisation
(+20 dB) recovered it into *decodable* audio but not into *correct* audio — the
capture did not contain the video. Whisper hallucinating fluent text on silence/noise
is the reason empty transcripts raise instead of being stored, and the reason a
capture must be confirmed by ear before its transcript is trusted.

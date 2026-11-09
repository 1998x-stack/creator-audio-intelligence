# Audio capture setup

Capture is the highest-risk part of this pipeline. `browser-harness`
`start_recording()` records **screenshots and action traces, not audio**; the audio
comes from a separate FFmpeg process reading an OS input. Getting that input right is
the difference between a real transcript and a transcript of the room.

## The intended route

```
Chrome audio output
        ↓
Virtual audio sink (loopback device)
        ↓
PulseAudio / PipeWire monitor   (Linux)      or   BlackHole input  (macOS)
        ↓
ffmpeg -f <backend> -i <capture input> -ac 1 -ar 16000 -c:a pcm_s16le out.wav
```

The recorder **starts before playback and stops on the page's real `ended` state**, so
the WAV brackets the playback interval.

## Linux (PulseAudio or PipeWire) — recommended

No admin rights are needed.

```bash
# 1. create the sink
pactl load-module module-null-sink sink_name=cai_sink \
    sink_properties=device.description=CAI_Audio

# 2. confirm the monitor now exists
pactl list short sources | grep cai_sink        # -> cai_sink.monitor

# 3. make it durable (survives reboot)
cat >> ~/.config/pulse/default.pa <<'PA'
load-module module-null-sink sink_name=cai_sink sink_properties=device.description=CAI_Audio
set-default-sink cai_sink
PA

# 4. point Chrome at it and validate BEFORE recording anything real
scripts/inspect_audio.sh --backend pulse --device cai_sink.monitor --seconds 5
```

`scripts/bootstrap_linux.sh` performs steps 1–2 and prints step 3.

Route Chrome into `cai_sink`. Setting the default sink usually works, but verify —
do not assume:

```bash
pactl list short sink-inputs          # which streams exist and where they point
pactl move-sink-input <index> cai_sink
```

**`@DEFAULT_MONITOR@` follows the *default* sink, which is not necessarily the sink
Chrome uses.** Prefer the explicit `cai_sink.monitor` name.

## macOS — a virtual device is mandatory

The built-in microphone **cannot** capture the built-in speakers here: a volume sweep
from 0 → 65 → 100 changed the recorded level by ~0 dB, and the capture was
unintelligible at +19 dB (GOTCHA-AUDIO-05). A virtual device is required.

```bash
brew install --cask blackhole-2ch          # requires your admin password
# Audio MIDI Setup -> create a Multi-Output Device containing
#   BlackHole 2ch + your speakers; set it as the system output.

scripts/inspect_audio.sh --backend avfoundation --device 'BlackHole 2ch' --seconds 5
```

Use the **device name**, not an index: indices shift when Bluetooth devices connect
and disconnect (GOTCHA-AUDIO-04).

## Preconditions that caused real failures

| Precondition | Failure if ignored |
|---|---|
| System output volume **> 0** | Chrome plays silently; the mic records the room and QC still says `ok` (GOTCHA-AUDIO-06) |
| Output device is the one the capture input can hear | You capture nothing, or capture a notification |
| Notifications/DND muted, unrelated apps quiet | Other audio is recorded into the WAV |
| Correct device by **name** | Indices change between runs (GOTCHA-AUDIO-04) |
| Headphones not connected | Output silently moves to Bluetooth and the capture path breaks |

## Validate before you trust

`scripts/inspect_audio.sh --device <name>` records N seconds and reports format, RMS,
peak/peak_dBFS, silence ratio, clipping fraction, sha256 and the QC verdict, exiting
non-zero when the capture is unusable. Then confirm **content**, which QC cannot:

```bash
ffprobe <capture>.wav
ffmpeg -hide_banner -i <capture>.wav -af volumedetect -f null -   # compare with a valid capture
.venv/bin/python scripts/asr_benchmark.py <capture>.wav           # does it transcribe sensibly?
```

A file that is valid, loud and still wrong is the failure mode this project has
actually hit. **QC passing is not evidence that the right audio was recorded.**

## QC gates applied automatically

`recorder.wav_quality()` rejects:

| Reason | Condition |
|---|---|
| `too_short` | < 2 s |
| `low_audio_energy` | RMS < 35 |
| `mostly_silent` | > 95 % of 10 ms frames below the silence threshold |
| `clipped` | > 2 % of samples at full scale |
| `duration_mismatch` | differs from the player's duration by > max(12 s, 25 %) |

The raw WAV is kept even on failure (with `qc_reason` in SQLite) so the failure is
diagnosable. ASR applies a **bounded in-memory** peak normalisation (≤ +20 dB) for
quiet captures; the stored file is never modified and the applied rule and gain are
recorded in `transcripts.model_version`.

## Safety

* Audio loopback records everything on that route, including notifications, meetings
  and private speech. Use a dedicated sink, mute notifications, and review before
  retaining.
* Do not record third-party content without permission. `--rights-ok` is an explicit
  operator assertion, not a licence check.
* Never expose Chrome's CDP port off-loopback (GOTCHA-SEC-01).

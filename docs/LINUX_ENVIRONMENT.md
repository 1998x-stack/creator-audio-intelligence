# Environment audit

## What was audited, and what the audit actually ran on

The specification targets a Linux server. **The machine that was available is a
macOS workstation.** That is recorded first because it determines which gates could
be executed and which are `NOT RUN` (see GOTCHA-ENV-02 and `docs/TEST_REPORT.md`).

Commands run: `uname -a`, `uname -m`, `/etc/os-release`, `python3 --version`,
`nproc`, `sysctl hw.memsize`, `df -h`, `command -v` for each tool, `ffmpeg -version`,
`ffmpeg -devices`, `ffmpeg -f avfoundation -list_devices`, `browser-harness --doctor`,
`curl http://127.0.0.1:9222/json/version`, `lsof -nP -iTCP -sTCP:LISTEN`,
`nvidia-smi`, `system_profiler SPAudioDataType`, `SwitchAudioSource -c`.

No credentials, cookies, tokens or personal data are recorded below.

## Observed environment

| Item | Value |
|---|---|
| Kernel / OS | `Darwin XdeMacBook-Pro-2.local 27.2.0 … RELEASE_ARM64_T8142` (macOS 27.2.0) |
| Architecture | `arm64` |
| CPU | Apple M5, 10 cores (`nproc` → 10) |
| RAM | 16 GB |
| Disk | `/` 926 GB total, 18 GB used, **658 GB free (3 %)** |
| Display session | GUI session present (Chrome window launches; `activate_tab` works) |
| Init / service manager | `launchd`; **no `systemd`**, no `systemctl` |
| Audio server | CoreAudio; **no PulseAudio/PipeWire**, no `pactl` |
| Virtual display | **no `Xvfb`** (not needed on macOS) |
| Python | `/opt/homebrew/bin/python3.12` → 3.12.15; `/usr/bin/python3` → 3.9.6 (too old) |
| FFmpeg | 9.0.2 (Homebrew). Audio inputs: **only `-f avfoundation`** |
| Chrome | 156.0.8078.81 at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| browser-harness | 0.1.13 (`uv tool install --python 3.12 --upgrade --force browser-harness`) |
| Ollama | **not installed** (no longer used; provider swapped to `iagent/standard`) |
| Whisper weights | `small` via `Systran/faster-whisper-small`, cached after first download |
| GPU | no NVIDIA GPU; `nvidia-smi` absent. ASR uses **CPU INT8** |

## Audio devices observed (macOS, avfoundation)

```
AVFoundation audio devices:
  [0] xie’s iPhone Microphone
  [1] WH-1000XM4
  [2] MacBook Pro Microphone
```

Output devices: `WH-1000XM4` (Bluetooth) and `MacBook Pro Speakers`. **There is no
loopback/screen-capture audio device**, which is the single largest environmental
blocker on this host. Device *indices* changed between runs (Bluetooth
connect/disconnect) — see GOTCHA-AUDIO-04.

An important non-obvious value: `osascript -e 'output volume of (get volume settings)'`
returned **0**, so Chrome was playing silently during the first capture attempt and
the microphone recorded the room. Always check the system output volume before a
capture (GOTCHA-AUDIO-06).

## ASR device decision

No CUDA device is present, so `device=cpu, compute_type=int8` is used. Measured on
this host: 48.5 s of Mandarin decoded in 7.2–14.8 s wall clock (realtime factor
≈ 0.15–0.30). No GPU comparison was attempted because no GPU exists.

## What a Linux target should provide

| Requirement | Check | Notes |
|---|---|---|
| Python ≥ 3.11 | `python3 -V` | 3.12 preferred; the system 3.9 on Debian/Ubuntu is too old |
| FFmpeg with the right input | `ffmpeg -devices \| grep -E 'pulse|alsa'` | Pulse/PipeWire build |
| Audio server | `pactl info` | PulseAudio or `pipewire-pulse` |
| Null sink | `pactl list short sources \| grep cai_sink.monitor` | created by `scripts/bootstrap_linux.sh` |
| Chrome/Chromium | `command -v google-chrome chromium` | 136+ **requires** a dedicated `--user-data-dir` for CDP |
| browser-harness | `browser-harness --version` | `uv tool install --python 3.12` |
| CDP reachable | `curl http://127.0.0.1:9222/json/version` | must be loopback only (GOTCHA-SEC-01) |
| Display for Chrome | `echo $DISPLAY` | only needed for headed Chrome; let Chrome's own binaries do the work |
| Disk | `df -h` | 16 kHz mono PCM16 ≈ 1.9 MB/min per video; budget for WAL and WAVs |

`scripts/doctor.sh` checks all of these on either platform and exits non-zero on a
blocking gap.

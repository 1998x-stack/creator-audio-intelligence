# Deployment

Target: a single Linux host, one non-root user, one recorder at a time.

> **Status: the systemd and launchd units below are provided but were NOT RUN.** The
> machine used for this work is macOS, so `systemd` could not be exercised. Everything
> in "Bootstrap" and "Running" was executed on macOS with the equivalents noted. See
> `docs/TEST_REPORT.md` §7 and GOTCHA-ENV-02.

## 1. Bootstrap

```bash
git clone https://github.com/1998x-stack/creator-audio-intelligence.git
cd creator-audio-intelligence
scripts/bootstrap_linux.sh          # venv, deps, browser-harness, cai_sink, DB
scripts/doctor.sh                   # read-only readiness gate; exits non-zero on a gap
```

`scripts/bootstrap_linux.sh` will not do anything privileged beyond package
installation: it prints the `~/.config/pulse/default.pa` snippet to make the audio sink
persistent instead of editing your files, and it enables no service.

Python **3.11+** is required (the system 3.9 on Debian/Ubuntu is too old —
GOTCHA-ENV-03).

## 2. Chrome for automation

Chrome 136+ refuses `--remote-debugging-port` on a normal profile, so a dedicated
profile is mandatory. It is also a security boundary: the profile holds live session
cookies, and CDP has **no authentication**.

```bash
scripts/launch_chrome_cdp.sh                  # binds 127.0.0.1 only, waits for readiness
export BU_CDP_URL=http://127.0.0.1:9222
browser-harness doctor
```

Never change the bind address off loopback (GOTCHA-SEC-01). If the host has no display
server, a headed Chrome under `Xvfb` is the path; **this was not tested here** and a
pure headless Chrome is *not* assumed to produce audio.

## 3. Audio

See `docs/LINUX_AUDIO_SETUP.md`. Short version:

```bash
pactl load-module module-null-sink sink_name=cai_sink sink_properties=device.description=CAI_Audio
scripts/inspect_audio.sh --backend pulse --device cai_sink.monitor --seconds 5
```

Validate **before** any real capture. A sink existing is not proof that Chrome routes
into it, and a QC-passing WAV is not proof that the right audio was captured
(GOTCHA-AUDIO-06).

## 4. Configuration

| Variable | Purpose | Default |
|---|---|---|
| `CAI_LLM_BASE_URL` | OpenAI-compatible base URL | `IAUTO_BASE_URL` or `https://iagent.iauto.com/v1` |
| `CAI_LLM_API_KEY` | bearer token (env only — never a CLI flag) | `IAUTO_API_KEY` |
| `CAI_LLM_MODEL` | model id | `iagent/standard` |
| `BU_CDP_URL` | CDP endpoint for browser-harness | `http://127.0.0.1:9222` |
| `BH_TAB_MARKER` | set `0` to keep page titles clean | unset |
| `CAI_CHROME_PORT` | CDP port | `9222` |
| `CAI_AUDIO_BACKEND` / `CAI_AUDIO_DEVICE` | capture route used by the test scripts | platform-dependent |

Put secrets in the service's `EnvironmentFile` (mode `0600`), never in the unit file
and never in `argv`.

## 5. Running

```bash
# discovery only (no capture, no rights needed)
cai discover --profile 'https://www.douyin.com/user/<verified_creator_id>' --scroll-rounds 3

# dry validation of the whole offline chain
scripts/e2e_test.sh --tier offline

# capture + ASR + LLM for up to 3 works
cai run \
  --profile 'https://www.douyin.com/user/<verified_creator_id>' \
  --backend pulse --device cai_sink.monitor \
  --limit 3 --rights-ok --llm-model iagent/standard

# recover after an ASR/LLM failure without re-recording
cai resume --llm-model iagent/standard

# inspect one result end to end
cai evidence --video-row-id 1
scripts/verify_sqlite.py data/creator_intel.sqlite3 --backup-to /var/backups/cai.sqlite3
```

Do **not** enable unattended history ingestion. Two P0 blockers apply on the machine
used here (unreachable target, no validated capture route), and the three-video gate
has not run.

## 6. systemd --user unit (NOT RUN)

`~/.config/systemd/user/cai.service`:

```ini
[Unit]
Description=Creator Audio Intelligence worker
After=default.target

[Service]
Type=oneshot
WorkingDirectory=%h/creator-audio-intelligence
# The environment file holds CAI_LLM_API_KEY etc. Keep it mode 0600.
EnvironmentFile=%h/.config/cai/env
Environment=BU_CDP_URL=http://127.0.0.1:9222
Environment=BH_TAB_MARKER=0
# A user service does NOT inherit the graphical session, so DISPLAY and the audio
# server socket must be set explicitly. Audio routing fails without this.
Environment=DISPLAY=:0
Environment=PULSE_SERVER=unix:%t/pulse/native
ExecStart=%h/creator-audio-intelligence/.venv/bin/cai resume --llm-model iagent/standard
# One recording at a time; a hard stop must not leave FFmpeg holding the sink.
KillSignal=SIGTERM
TimeoutStopSec=60

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user start cai.service
systemctl --user status cai.service
journalctl --user -u cai.service -n 100 --no-pager
```

Design notes and untested assumptions:

* `Type=oneshot` on purpose: a timer-driven `resume` pass is much easier to reason
  about than a resident loop, and `resume` already skips completed stages.
* `KillSignal=SIGTERM` matters: the recorder converts SIGTERM into an exception so
  FFmpeg is stopped (GOTCHA-AUDIO-03). `SIGKILL` still leaves an orphan, which
  `run_profile`/`reap_orphans` cleans up on the next start.
* `PULSE_SERVER` and `DISPLAY` in a user unit are the usual stumbling block. Verify
  `systemctl --user show-environment` rather than assuming.
* Do not add `Restart=always` until the failure taxonomy has been observed in
  production; `attempts` is capped at 3 per video and the lock is single-host.

A `launchd` equivalent would be required for macOS; it is not provided because a
resident service is not the recommended pattern until the three-video gate passes.

## 7. Backups

WAL means a plain file copy of the `.sqlite3` can be inconsistent. Use the backup API:

```bash
python scripts/verify_sqlite.py data/creator_intel.sqlite3 --backup-to /var/backups/cai-$(date +%F).sqlite3
```

This verifies integrity, then writes and re-verifies a consistent backup including
row-count comparison. WAV files are large and remain on local disk; back them up
separately and treat them as controlled content.

## 8. Upgrades

`data/**`, `*.sqlite3`, media and the Chrome profile are gitignored. The schema uses
`CREATE TABLE IF NOT EXISTS` with no migration framework (v0.2.x), so before upgrading
with real data present: back up, run `scripts/verify_sqlite.py`, upgrade, re-run it.
There is no `schema_version` table yet.

## 9. Troubleshooting order

1. `scripts/doctor.sh` — toolchain, CDP, audio device class, DB, LLM.
2. `scripts/smoke_test.sh` — import path, tests, temporary DB.
3. `scripts/inspect_audio.sh --device <name>` — capture route, with remediation.
4. `scripts/e2e_test.sh --tier live-browser` — real Chrome discovery + playback.
5. `docs/gotchas.md` — every fault observed during this work, with root causes.

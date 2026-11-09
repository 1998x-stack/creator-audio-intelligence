# Gotchas — real faults, root causes and verification

Every entry below was **observed on this machine** while deploying and testing the
project, unless it is explicitly marked `Potential / Not Reproduced`. Reproduction
commands were actually run; the "Actual" sections quote real output.

Rules for this file:

* Do not add a "gotcha" that has not been seen. Speculative risks go in
  [Potential / Not Reproduced](#potential--not-reproduced).
* An entry is only `Fixed` when a verification command is recorded that now passes.
* `Blocked` means an external condition prevents verification. It is never a
  synonym for "probably fine".

## Environment summary for all entries

| | |
|---|---|
| OS | macOS 27.2.0 (Darwin 27.2.0), arm64, Apple M5, 16 GB — **not Linux** |
| Python | 3.12.15 (`/opt/homebrew/bin/python3.12`); system `python3` is 3.9.6 |
| Chrome | 156.0.8078.81, dedicated profile `~/.cai/chrome-profile`, CDP on `127.0.0.1:9222` |
| Browser Harness | 0.1.13 (uv tool), `BU_CDP_URL=http://127.0.0.1:9222` |
| FFmpeg | 9.0.2 (Homebrew); only `-f avfoundation` as an audio input |
| Audio Backend | CoreAudio via avfoundation; **no virtual loopback device installed** |
| Whisper | faster-whisper 1.2.1, CTranslate2 4.8.5, `small`, CPU, int8 |
| LLM | `iagent/standard` at `https://iagent.iauto.com/v1` (OpenAI-compatible) |

---

## GOTCHA-ENV-01 — douyin.com is unreachable: TCP connects, then the TLS handshake is reset

### Severity
P0

### Status
Blocked

### Environment
- OS: macOS 27.2.0 arm64
- Python: 3.12.15
- Chrome: 156.0.8078.81
- Browser Harness: 0.1.13
- FFmpeg: 9.0.2
- Audio Backend: CoreAudio
- Whisper: n/a
- Ollama: n/a (replaced by iagent)

### Symptoms
`curl https://www.douyin.com/` fails with `Connection reset by peer`. Chrome shows
`ERR_CONNECTION_RESET`. Everything else on the machine works: GitHub, PyPI,
Hugging Face, `iagent.iauto.com`, `baidu.com`, `google.com` all fetch normally.

### Reproduction
```bash
curl -v -m 12 -o /dev/null https://www.douyin.com/
# ... Connected to www.douyin.com (36.156.33.201) port 443
# ... (304) (OUT), TLS handshake, Client hello (1):
# ... Recv failure: Connection reset by peer
curl -sS -m 10 -o /dev/null -w '%{http_code}\n' http://www.douyin.com/    # 000
curl -sS -m 10 --tlsv1.2 --tls-max 1.2 -o /dev/null -w '%{http_code}\n' https://www.douyin.com/   # 000
curl -sS -m 10 -k --resolve www.douyin.com:443:36.156.33.210 -o /dev/null -w '%{http_code}\n' https://www.douyin.com/  # 000
```

### Expected Behavior
The page loads (HTTP 200) and browser-harness can read its DOM.

### Actual Behavior
TCP connect succeeds; the connection is reset immediately after (or during) the
TLS `ClientHello`. Identical for HTTP/80, for TLS 1.2 forced, and for a direct CDN
IP. DNS resolves correctly and `nc -z www.douyin.com 443` reports success, so this
is not DNS or plain TCP reachability.

Same behaviour observed for `v.douyin.com`, `www.toutiao.com`, `www.bilibili.com`
and `weibo.com`; `baidu.com`, `zhihu.com`, `example.com`, `google.com` all work.

### Root Cause
Network-level filtering of the TLS handshake for these hosts (SNI-based
interference by an intermediary), not an application defect.

### Diagnosis
| Command | Result |
|---|---|
| `dig +short www.douyin.com` | resolves (CDN addresses) |
| `nc -z -v www.douyin.com 443` | succeeds |
| `curl -v https://www.douyin.com/` | RST after `Client hello` |
| `curl https://www.baidu.com/` | `HTTP 200` |
| Chrome via CDP | `chrome-error://chromewebdata/`, body `This site can't be reached … ERR_CONNECTION_RESET` |

### Fix
None attempted. **Working around a network access control is out of scope for this
project**, and the operator confirmed the site is expected to be blocked here.

### Verification
Not verified — cannot be verified from this network. Gate C/D discovery against
Douyin is therefore reported as **BLOCKED**, and `scripts/e2e_test.sh --tier
live-douyin` exits `3` with this reason rather than pretending to run.

### Prevention
* `cai discover` and `BrowserHarness.discover()` classify a navigation error page as
  `ACCESS_DENIED` (see GOTCHA-BROWSER-03), so this surfaces as a named condition, not
  as "0 videos found".
* Run the Douyin tiers from a network that can reach the site.

### References
`src/creator_audio_intel/browser.py` (`_classify_empty`), `scripts/e2e_test.sh`
(`tier_live_douyin`)

---

## GOTCHA-ENV-02 — the host is macOS, so Linux-only gates cannot be executed here

### Severity
P1

### Status
Blocked

### Environment
All rows of the summary table above.

### Symptoms
The task specification targets a Linux server (PulseAudio/PipeWire monitor,
`systemd --user`, Xvfb). This machine is an Apple Silicon Mac with CoreAudio and
`launchd`.

### Reproduction
```bash
uname -a                       # Darwin ... arm64
command -v pactl systemctl Xvfb   # all missing
command -v ffmpeg SwitchAudioSource # present
```

### Expected Behavior
On Linux: `pactl load-module module-null-sink` creates `cai_sink` +
`cai_sink.monitor`, and `ffmpeg -f pulse -i cai_sink.monitor` records it.

### Actual Behavior
`pactl`, `systemctl` and `Xvfb` do not exist. FFmpeg 9.0.2 here offers only
`-f avfoundation`.

### Root Cause
Platform mismatch between the specification and the machine provided.

### Fix
Rather than simulate Linux, the work was made portable and the platform-specific
gaps were isolated:
* `recorder.KNOWN_BACKENDS` already covers `pulse` / `avfoundation` / `dshow`;
  `ffmpeg_audio_devices()` branches per platform.
* `docs/LINUX_AUDIO_SETUP.md` and `scripts/bootstrap_linux.sh` carry the Linux
  procedure, written but **not executed** here.
* `docs/LINUX_DEPLOYMENT.md` carries the systemd user unit, written but **NOT RUN**.

### Verification
Linux-only items are reported as `NOT RUN` in `docs/TEST_REPORT.md`, with the
reason. Everything platform-independent was executed for real and is recorded as
such.

### Prevention
`scripts/e2e_test.sh --tier` prints the reason for every `SKIP` and exits `3`; a
Linux run is a re-run of the same commands, not a rewrite.

### References
`docs/LINUX_AUDIO_SETUP.md`, `docs/LINUX_DEPLOYMENT.md`, `scripts/bootstrap_linux.sh`

---

## GOTCHA-ENV-03 — the system Python is too old for the package

### Severity
P2

### Status
Fixed

### Environment
- Python: `/usr/bin/python3` = 3.9.6

### Symptoms
`pip install -e '.[asr,dev]'` with the system interpreter fails the
`requires-python = ">=3.11"` gate.

### Reproduction
```bash
/usr/bin/python3 --version        # Python 3.9.6
/usr/bin/python3 -m venv /tmp/x && /tmp/x/bin/pip install -e .
# ERROR: Package 'creator-audio-intelligence' requires a different Python: 3.9.6 not in '>=3.11'
```

### Expected Behavior
Install succeeds on a supported interpreter.

### Actual Behavior
Install refused.

### Root Cause
macOS ships a 3.9 interpreter as `/usr/bin/python3`; the project (reasonably)
requires 3.11+ for `datetime.UTC`-era typing and `X | Y` syntax at runtime.

### Diagnosis
`brew list --versions python@3.12`; `uv python list`

### Fix
Use an explicit interpreter: `/opt/homebrew/bin/python3.12 -m venv .venv`. Recorded
in `scripts/bootstrap_linux.sh`, which now fails loudly when the interpreter is
< 3.11, and in `docs/LINUX_ENVIRONMENT.md`.

### Verification
```bash
source .venv/bin/activate && python -V   # Python 3.12.15
python -m pytest -q                      # 53 passed
```

### Prevention
`scripts/doctor.sh` reports the interpreter; `bootstrap_linux.sh` gates on
`sys.version_info >= (3, 11)`.

### References
`pyproject.toml` (`requires-python`), `scripts/bootstrap_linux.sh`

---

## GOTCHA-BROWSER-01 — `goto_url` + `wait_for_load()` can report the *previous* page

### Severity
P1

### Status
Fixed

### Environment
- Chrome: 156.0.8078.81
- Browser Harness: 0.1.13

### Symptoms
With a real recording run, the first video failed with
`Unexpected navigation while recording`, while the identical sequence typed by hand
worked. The failure was intermittent and depended on how the previous page had been
opened.

### Reproduction
```python
b = BrowserHarness()
b.open('http://127.0.0.1:8099/user/local-e2e-creator')   # uses new_tab
b.open('http://127.0.0.1:8099/video/9000000000000000000')  # uses goto_url
b.inspect_player()['url']   # may still be the /user/ page
```

### Expected Behavior
`open()` returns only once the requested document is the one loaded.

### Actual Behavior
`goto_url()` is `Page.navigate`, which returns *before* navigation happens.
`wait_for_load()` then evaluated `document.readyState` on the still-current
document (already `complete`) and returned immediately, so the next call read the
old page.

### Root Cause
A missing post-condition: navigation was issued but never confirmed.

### Diagnosis
Traced every poll of `inspect_player()` during a failed run; all polls showed the
*video* URL, so the bad observation had to come from before the first poll. Adding
a `page_info()` read immediately after `open()` reproduced it.

### Fix
`BrowserHarness.open()` computes the target URL (minus fragment), navigates, then
polls until **both** `location.href` matches the target **and**
`document.readyState === 'complete'`, with a 25 s budget, before returning. It also
retries once when the harness' 5 s IPC budget is exceeded.

### Verification
```bash
.venv/bin/cai --db data/e2e.sqlite3 run --platform fixture --profile \
  http://127.0.0.1:8099/user/local-e2e-creator --backend avfoundation \
  --device ':MacBook Pro Microphone' --limit 1 --scroll-rounds 1 --rights-ok
# -> "completed": 1,  failed: []
```
Regression test: `tests/test_regressions_browser.py::test_gotcha_browser_navigation_race_waits_for_target_document`

### Prevention
The condition is asserted in code (poll for URL + readyState), so "navigate" cannot
be mistaken for "navigated" again.

### References
`src/creator_audio_intel/browser.py` (`BrowserHarness.open`)

---

## GOTCHA-BROWSER-02 — browser-harness rewrites page titles with a tab marker

### Severity
P3

### Status
Fixed

### Symptoms
Titles stored in SQLite began with a horse emoji, e.g. `🐴 HTML Video`, because the
harness marks its attached tab by prefixing the document title.

### Reproduction
```bash
browser-harness <<'PY'
new_tab("https://example.com"); wait_for_load(); print(page_info()['title'])
PY
# '🐴 Example Domain'
```

### Expected Behavior
Stored titles are the page's own titles.

### Actual Behavior
The marker leaked into `videos.title`, i.e. into the system of record.

### Root Cause
The harness' tab marker is on by default (`BH_TAB_MARKER=0` disables it).

### Fix
`_clean_title()` strips the marker defensively, and `scripts/e2e_test.sh` exports
`BH_TAB_MARKER=0`.

### Verification
```bash
BU_CDP_URL=http://127.0.0.1:9222 BH_TAB_MARKER=0 .venv/bin/cai discover --platform fixture \
  --profile http://127.0.0.1:8099/user/local-e2e-creator
sqlite3 data/e2e.sqlite3 "select title from videos;"
# fixture work 01_macro  (no leading emoji)
```
Regression test: `test_gotcha_browser_title_marker_stripped`

### References
`src/creator_audio_intel/browser.py` (`_clean_title`, `_TITLE_MARKER`)

---

## GOTCHA-BROWSER-03 — a zero-result discovery was returned as a silent empty list

### Severity
P1

### Status
Fixed

### Symptoms
`cai discover` printed `{"visible_videos_found": 0}` for a homepage with no works,
for a login wall, and for a page whose markup had changed — three completely
different operational situations, indistinguishable in the output.

### Reproduction
```bash
curl -s http://127.0.0.1:8099/user/loginwall | head -3   # a login wall
.venv/bin/cai discover --platform fixture --profile http://127.0.0.1:8099/user/loginwall
```

### Expected Behavior
The operator must be able to tell "this creator has no works" from "we are blocked"
from "our selector is stale".

### Actual Behavior
All three yielded `visible_videos_found: 0` and exit code 0.

### Root Cause
`discover()` returned `list(seen.values())` unconditionally; the empty case carried
no information.

### Diagnosis
Built the three pages in `scripts/serve_fixture.py` and ran discovery against each;
all produced the same output.

### Fix
`discover()` now raises `DiscoveryError` with a machine-readable code:
`EMPTY_PROFILE`, `DISCOVERY_FAILED`, `LOGIN_REQUIRED`, `ACCESS_DENIED`,
`DOM_CHANGED`, `TIMEOUT`. Classification order matters: an error page or login wall
is checked *before* the anchor count, because a login wall also has no video links.
The detail string carries the anchor/video counts for triage.

### Verification
```bash
PYTHONPATH=src python - <<'PY'
from creator_audio_intel.browser import BrowserHarness, DiscoveryError
b = BrowserHarness(timeout=90)
for name in ('empty', 'loginwall', 'broken'):
    try:
        b.discover(f'http://127.0.0.1:8099/user/{name}', 0, platform='fixture')
    except DiscoveryError as exc:
        print(name, exc.code)
PY
# empty EMPTY_PROFILE / loginwall LOGIN_REQUIRED / broken DOM_CHANGED
# and an unreachable host -> ACCESS_DENIED
```
Regression tests: `test_gotcha_discovery_taxonomy` (parametrised),
`test_gotcha_discovery_rejects_unknown_code`

### Prevention
`scripts/e2e_test.sh` asserts all three negatives on every live-browser run, so a
future selector change cannot silently degrade to "0 found".

### References
`src/creator_audio_intel/browser.py` (`DiscoveryError`, `_classify_empty`)

---

## GOTCHA-BROWSER-04 — `scroll()` times out on a background tab

### Severity
P2

### Status
Fixed

### Symptoms
Discovery on the fixture homepage raised
`Input.dispatchMouseEvent timed out after 5s waiting for the daemon`, aborting the
whole run.

### Reproduction
```bash
# with the automation tab in the background
browser-harness <<'PY'
new_tab("https://example.com"); wait_for_load()
scroll(500, 450, dy=650)
PY
# browser_harness.helpers._IPCResponseTimeout: Input.dispatchMouseEvent timed out
```

### Expected Behavior
Scrolling a page should work regardless of whether the tab is the visible one.

### Actual Behavior
Background renderers are throttled and the input event is not acknowledged within
the 5 s IPC budget.

### Root Cause
Documented browser-harness behaviour: a timed-out `scroll()` on an attached
background tab is evidence that the page needs to be visible.

### Diagnosis
`browser-harness doctor` reported an active connection and
`document.hasFocus() === false` for the attached tab.

### Fix
`_scroll_once()` scrolls; on failure it calls `activate_tab(current_tab())`, waits,
and retries the *same* scroll exactly once. If it still fails it returns `False`,
`discover()` stops scrolling, and whatever was observed is still reported — no
`Runtime.evaluate` scroll replacement is invented, per the harness guidance.

### Verification
```bash
python -m pytest tests/test_regressions_browser.py::test_gotcha_scroll_on_background_tab_activates_and_retries -q
# 1 passed  (asserts: first scroll fails, second succeeds after activation)
```
Live: discovery of 3 works from a real homepage succeeded on the first attempt
after this change.

### Prevention
The retry budget is 1, not a loop, so a genuinely broken tab still fails fast.

### References
browser-harness `SKILL.md` ("A timed-out `scroll(...)` on an attached background
tab is evidence that the page needs to be visible"), `browser.py::_scroll_once`

---

## GOTCHA-BROWSER-05 — the "largest visible video" can change element mid-recording

### Severity
P1

### Status
Fixed

### Symptoms
`inspect_player()` re-derived its target from the DOM on every call
("largest visible `<video>`"). On any page that swaps or re-renders players, the
polled `currentTime` could come from a *different* element than the one being
recorded — which would attribute one video's audio to another with no error.

### Reproduction
Load any page with two `<video>` elements of similar size and hide the larger one
mid-playback; the next `inspect_player()` reports the other element's time.

### Expected Behavior
One video, one element, for the whole record lifecycle.

### Actual Behavior
No stability guarantee; selection was recomputed per call.

### Root Cause
Selection was a pure function of the current DOM, so it had no memory.

### Diagnosis
`_PLAYER_JS` contained no element identity — it re-sorted `querySelectorAll('video')`
each poll.

### Fix
`prepare_player()` tags the chosen element `data-cai-player="1"` (clearing stale
tags) and `_SELECT` prefers the pinned element. `inspect_player()` reports `pinned`,
and `wait_for_end()` fails closed with
`Pinned player element disappeared (player swapped mid-recording)` if the pin is
lost. `prepare_player()` also `scrollIntoView`s so the element is clickable.

### Verification
```bash
.venv/bin/cai --db data/e2e.sqlite3 run --platform fixture ... --limit 3 --rights-ok
# each recording reported pinned=True for every poll (trace), completed with failed: []
```
Regression test: `test_gotcha_player_must_be_pinned_across_recording`

### Prevention
Fail-closed on pin loss; the invariant is asserted in `wait_for_end`, not merely
documented.

### References
`src/creator_audio_intel/browser.py` (`_SELECT`, `_PREPARE_JS`, `wait_for_end`)

---

## GOTCHA-BROWSER-06 — `duration` is `null` until `loadedmetadata`, and a JS-side wait breaks the IPC budget

### Severity
P1

### Status
Fixed

### Symptoms
Two related failures:
1. `No suitable finite-duration HTML5 player (2s-60min)` on a page that played fine
   in the browser.
2. After switching to a JS-side wait for metadata:
   `Runtime.evaluate timed out; expression: (async()=>{...})`.

### Reproduction
```python
b = BrowserHarness(); b.open('http://127.0.0.1:8099/video/9000000000000000000')
b.inspect_player()['duration']   # None, readyState 0  -> the old code refused to record
```

### Expected Behavior
The duration gate should run once metadata is actually available.

### Actual Behavior
`duration` was read immediately after `prepare_player()` (which happens right after
navigation), returning `null`; the code treated that as "no usable player".

### Root Cause
`HTMLMediaElement.duration` is only populated at `loadedmetadata`. Separately,
`browser-harness`'s IPC response budget is **5 s**
(`DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS = 5.0`), so awaiting a 15 s `loadedmetadata`
promise inside the page times out the transport.

### Diagnosis
```bash
grep -n 'DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS' \
  ~/.local/share/uv/tools/browser-harness/lib/python3.12/site-packages/browser_harness/helpers.py
# DEFAULT_IPC_RESPONSE_TIMEOUT_SECONDS = 5.0
```

### Fix
`wait_for_metadata(timeout)` polls from the host with short, non-blocking reads
(`_METADATA_JS` returns immediately and only nudges `preload='auto'; load()` for
players that do not preload). `capture_video()` calls it before the duration gate.
No `await` remains inside any injected script.

### Verification
```bash
.venv/bin/cai --db data/e2e.sqlite3 run --platform fixture ... --limit 1 --rights-ok
# completed: 1  (the metadata gate passed on a real 48s player)
```
Regression test: `test_gotcha_metadata_is_polled_not_awaited_in_js`

### References
`src/creator_audio_intel/browser.py` (`wait_for_metadata`, `_METADATA_JS`),
`pipeline.capture_video`

---

## GOTCHA-BROWSER-07 — autoplay needs a gesture *and* a visible element in a focused tab

### Severity
P1

### Status
Fixed

### Symptoms
`{"ok": false, "reason": "NotAllowedError: play() failed because the user didn't
interact with the document first"}`. The existing click fallback then silently did
nothing, because it clicked the centre of a player that was **below the fold**.

### Reproduction
```bash
BU_CDP_URL=http://127.0.0.1:9222 browser-harness <<'PY'
new_tab("https://www.w3schools.com/html/html5_video.asp"); wait_for_load()
print(js("document.querySelector('video').getBoundingClientRect().toJSON()"))
# {'y': 857.45, ...}   viewport height is 724 -> the click at y=857 hit nothing
PY
```

### Expected Behavior
Playback starts, or the failure explains why.

### Actual Behavior
The gesture click used `box.y` unclamped and un-scrolled, so a below-the-fold player
received no click; the returned state was still `paused: true`.

### Root Cause
Two independent conditions: Chrome requires *user activation* for autoplay, and a
CDP click at coordinates outside the viewport does not land on the element.
Additionally, with `document.hasFocus() === false` (background tab) the gesture does
not take effect at all.

### Diagnosis
`document.hasFocus()` returned `false` for the attached tab, and the reported box
was outside the viewport.

### Fix
* `prepare_player()` calls `scrollIntoView({block:'center'})` and returns `inViewport`
  plus the box.
* `play()` only clicks when `inViewport` is true.
* If `document.hasFocus()` is false, `prepare_player()` activates the harness' own
  tab once and re-checks focus (reported as `focused` / `activated_tab`).

### Verification
```bash
# live, against the fixture site
prepare: {"ok": true, "inViewport": true, "focused": true, "activated_tab": true}
metadata: {"ok": true, "duration": 48.51675, "readyState": 4}
play: {"ok": true, "paused": false, "currentTime": 0.29}
# +6s -> currentTime 6.57 (1x realtime)
```
`scripts/e2e_test.sh --tier live-browser` asserts that `currentTime` advances
≥1.5 s in 3 s while the pin holds.

### Prevention
Activation is done only when focus is actually missing, so the operator's foreground
window is not stolen unnecessarily.

### References
`src/creator_audio_intel/browser.py` (`prepare_player`, `play`, `_FOCUS_JS`)

---

## GOTCHA-BROWSER-08 — two concurrent browser-harness invocations share one tab

### Severity
P2

### Status
Mitigated

### Symptoms
While two shell commands ran in parallel, one script's `new_tab("https://example.com")`
was followed by `page_info()` returning the *other* command's URL. Results were
attributed to the wrong page.

### Reproduction
```bash
BU_CDP_URL=http://127.0.0.1:9222 browser-harness <<'PY' &   # opens example.com
new_tab("https://example.com"); wait_for_load()
print(page_info()['url'])
PY
BU_CDP_URL=http://127.0.0.1:9222 browser-harness <<'PY'     # runs at the same time
new_tab("https://www.w3schools.com/html/html5_video.asp"); wait_for_load()
print(page_info()['url'])
PY
wait
# both print the w3schools URL
```

### Expected Behavior
Independent invocations should not disturb each other.

### Actual Behavior
One daemon holds exactly one attached tab; concurrent invocations interleave.

### Root Cause
By design in browser-harness: the attached-tab "horse marker" is daemon state.

### Diagnosis
`browser-harness doctor` shows a single `active browser connections — 1`, and one
`current_tab()`.

### Fix
Mitigated rather than fixed: the project is single-writer by design
(`RunLock`, `pipeline.run_profile` processes one video at a time) and the
documentation now states the rule explicitly. `scripts/e2e_test.sh` never runs
browser steps concurrently.

### Verification
All recorded runs were strictly serial; `cai run` holds `RunLock` for the whole
capture loop.

### Prevention
`RunLock` prevents two CAI workers. Do not add parallel browser steps without a
separate daemon name (`BU_NAME=`).

### References
`src/creator_audio_intel/lock.py`, browser-harness `SKILL.md`

---

## GOTCHA-AUDIO-01 — RMS-only quality control accepts clipped and near-silent captures

### Severity
P1

### Status
Fixed

### Symptoms
`wav_quality()` only checked format, duration and RMS. A recording that clipped
badly, or that was 97 % digital silence plus audible hiss, both passed as `ok`.

### Reproduction
```python
from creator_audio_intel.recorder import wav_quality
# a square wave at full scale (clipped) previously returned qc_ok=True with reason 'ok'
```

### Expected Behavior
Obviously unusable audio must be rejected with a specific reason.

### Actual Behavior
Only `too_short`, `low_audio_energy` and `duration_mismatch` existed; a loud
distorted capture passed.

### Root Cause
RMS is a single aggregate statistic: it cannot distinguish "loud and clean" from
"loud and clipped", nor "quiet speech" from "silence with a noise floor".

### Diagnosis
Generated a full-scale square wave and a 3 %-signal file; both passed the old check.

### Fix
`analyze_wav()` now also computes peak/peak_dBFS, clipped sample count and fraction,
per-10 ms silence ratio and format validity. `wav_quality()` rejects
`mostly_silent` (>95 % silent frames) and `clipped` (>2 % samples at full scale), and
the new metrics are carried on `CaptureResult` into SQLite.

### Verification
```bash
python -m pytest tests/test_regressions_audio.py -q
# 3 passed, incl. clipping -> 'clipped' and hiss -> 'mostly_silent'
ffmpeg -hide_banner -i <real capture> -af volumedetect -f null -
# mean_volume: -40.8 dB  max_volume: -19.7 dB   -> reported, and rejected as too quiet
```

### Prevention
QC reasons are stored per capture (`captures.qc_reason`) and aggregated by
`scripts/verify_sqlite.py`.

### References
`src/creator_audio_intel/recorder.py` (`analyze_wav`, `wav_quality`)

---

## GOTCHA-AUDIO-02 — a crashed run leaves an orphan FFmpeg holding the audio device

### Severity
P1

### Status
Fixed

### Symptoms
After a hard interruption, the next recording failed with "FFmpeg exited immediately"
and `ps` showed a stray `ffmpeg -f avfoundation -i :2` still holding the input.

### Reproduction
```bash
.venv/bin/cai --db data/x.sqlite3 run ... & sleep 8; kill -9 $!
pgrep -fl ffmpeg          # orphan survives the parent
```

### Expected Behavior
Nothing from a dead run should still hold the device.

### Actual Behavior
The recorder only tracked `self.proc` in memory; a SIGKILLed parent lost that handle
and left the child running.

### Root Cause
No on-disk record of the child process, so recovery had nothing to work from.

### Diagnosis
`pgrep -fl ffmpeg` after a killed run; `ls data/audio/**/*.ffmpeg.pid` found nothing
(no pidfile existed at all before the fix).

### Fix
`FFmpegLoopback.start()` writes `<wav>.ffmpeg.pid`; `stop()`/`abort()` remove it.
`reap_orphans(data_dir)` scans for those pidfiles and terminates only processes that
are (a) alive, (b) recorded by a pidfile under the data directory, and (c) whose
command line actually contains `ffmpeg`. `run_profile()` reaps on start and reports
`orphan_ffmpeg_reaped` in its result.

### Verification
```bash
python -m pytest tests/test_regressions_audio.py -q
# test_gotcha_orphan_ffmpeg_reaping_kills_real_ffmpeg  -> [1234] killed with SIGTERM
# test_gotcha_orphan_ffmpeg_reaping_ignores_unrelated_pids -> [] killed, pidfile kept
```

### Prevention
The guard against pid reuse is deliberately conservative: a pid whose command line
is not ffmpeg is logged and left alone, never killed.

### References
`src/creator_audio_intel/recorder.py` (`reap_orphans`), `pipeline.run_profile`

---

## GOTCHA-AUDIO-03 — SIGTERM does not unwind Python, so cleanup never runs

### Severity
P1

### Status
Fixed

### Symptoms
`kill <pid>` on a running capture left the orphan described in GOTCHA-AUDIO-02, and
the video row stayed in `recording` with a partial `.partial.wav`.

### Reproduction
```bash
python - <<'PY'
import os, signal, time
print('pid', os.getpid())
time.sleep(30)
PY
kill -TERM <pid>     # process dies immediately; no finally/except block runs
```

### Expected Behavior
`capture_video`'s `except BaseException: recorder.abort()` should stop FFmpeg and
mark the row failed.

### Actual Behavior
The default SIGTERM disposition terminates the interpreter without raising, so the
handler in `capture_video` was bypassed entirely.

### Root Cause
SIGTERM's default action is process termination, not an exception.

### Diagnosis
`python -c 'import signal; print(signal.getsignal(signal.SIGTERM))'` →
`Handlers.SIG_DFL`.

### Fix
`recorder.terminate_as_exception()` converts SIGTERM into a `KeyboardInterrupt` for
the duration of the recording and restores the previous handler afterwards;
`capture_video` wraps the record window in it.

### Verification
```bash
python -m pytest tests/test_regressions_audio.py -q
# test_gotcha_sigterm_must_unwind_python_so_the_recorder_is_aborted  -> KeyboardInterrupt raised
# test_gotcha_sigterm_handler_is_restored                            -> previous handler back
```

### Prevention
`db.reset_interrupted()` still reclassifies any `recording` row as `failed` on the
next start, so even SIGKILL is recoverable.

### References
`src/creator_audio_intel/recorder.py` (`terminate_as_exception`), `db.reset_interrupted`

---

## GOTCHA-AUDIO-04 — AVFoundation device *indices* are not stable

### Severity
P2

### Status
Fixed

### Symptoms
The same `-i ":2"` that selected `MacBook Pro Microphone` in one run selected a
different device in the next; `ffmpeg -list_devices` output changed between runs on
the same machine.

### Reproduction
```bash
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | sed -n '/audio devices/,$p'
# run 1: [0] iPhone Mic, [1] WH-1000XM4, [2] MacBook Pro Microphone
# run 2: [0] MacBook Pro Microphone
```

### Expected Behavior
A recorded capture device should be reproducible.

### Actual Behavior
Indices shifted when Bluetooth/Continuity devices connected or disconnected, so a
stored `--device :2` could point at headphones or at nothing.

### Root Cause
AVFoundation enumerates devices in discovery order.

### Diagnosis
Ran the enumeration twice while the Bluetooth headset dropped out.

### Fix
`ffmpeg_audio_devices()` returns device **names** (directly usable as
`-i ":<name>"`), and `scripts/inspect_audio.sh` prints names. Per the fix for
GOTCHA-ENV-02, `docs/LINUX_AUDIO_SETUP.md` recommends `cai_sink.monitor` on Linux,
which is already a stable name.

### Verification
```bash
ffmpeg -f avfoundation -i ":MacBook Pro Microphone" -t 1 -y /tmp/p.wav   # exit 0
python -c "from creator_audio_intel.recorder import ffmpeg_audio_devices as f; print(f())"
# ['xie’s iPhone Microphone', 'WH-1000XM4', 'MacBook Pro Microphone']
```

### Prevention
Documented as "use the name, not the index" in `docs/LINUX_AUDIO_SETUP.md` and in
the `inspect_audio.sh` output.

### References
`src/creator_audio_intel/recorder.py` (`ffmpeg_audio_devices`)

---

## GOTCHA-AUDIO-05 — macOS suppresses the playback signal on a speaker→microphone capture route

### Severity
P0

### Status
Blocked

### Symptoms
A real end-to-end run recorded successfully (QC passed, 45.5 s, RMS 300) but
produced nonsense when transcribed: `字幕by索兰娅` (a known Whisper hallucination),
and zero segments with VAD enabled. Raising the system volume from 0 → 65 → 100 and
`afplay -v 1.4` changed the recorded level from −19.5 dBFS to −22.1 dBFS — i.e. it did
not go up at all.

### Reproduction
```bash
# speakers -> built-in microphone, no virtual device
( ffmpeg -f avfoundation -i ":MacBook Pro Microphone" -vn -ac 1 -ar 16000 \
         -c:a pcm_s16le -t 14 -y /tmp/rec.wav & sleep 1.5; \
  afplay -v 1.4 data/fixtures/01_macro.wav & sleep 16; wait ) >/dev/null 2>&1
ffmpeg -hide_banner -i /tmp/rec.wav -af volumedetect -f null - 2>&1 | grep volume
# mean_volume: -40.4 dB   max_volume: -22.1 dB   (unchanged vs volume 0)
python -c "
import sys; sys.path.insert(0,'src')
from creator_audio_intel.asr import peak_normalize
from faster_whisper import WhisperModel
a,g = peak_normalize('/tmp/rec.wav'); print('gain', g)
m = WhisperModel('small', device='cpu', compute_type='int8')
print(''.join(s.text for s in m.transcribe(a, language='zh', vad_filter=False)[0]))
"
# gain 19.06
# 字幕by索兰娅
```

### Expected Behavior
With speakers at full volume and the microphone nearby, the recorded level should
rise with playback volume and be intelligible.

### Actual Behavior
Playback volume had essentially no effect on the recording, and the content was not
intelligible even after +19 dB of normalisation.

### Root Cause
Confirmed effect, mechanism consistent with the OS: macOS applies echo
cancellation / voice processing to the built-in microphone input, which suppresses
the built-in speaker output from the capture. **Hypothesis** is `AEC`; the alternative
"the speakers are simply too quiet" is ruled out by the volume sweep showing no
level change. An unmodified fixture WAV transcribes at CER 1.11 %, so the ASR path
itself is sound.

### Diagnosis
| Measurement | Volume 0 | Volume 65 | Volume 100 + afplay 1.4 |
|---|---|---|---|
| mean volume | −40.8 dB | −37.3 dB | −40.4 dB |
| max volume | −19.7 dB | −19.5 dB | −22.1 dB |
| transcript | nonsense | nonsense | nonsense |

### Fix
**Not fixable in application code.** A virtual audio device is required:
* macOS: `brew install --cask blackhole-2ch`, then a Multi-Output Device containing
  both BlackHole 2ch and the speakers, and
  `--backend avfoundation --device 'BlackHole 2ch'`. This needs an admin password and
  a change to how the operator's audio is routed, so it was left for the operator.
* Linux (the specification's target): a PulseAudio/PipeWire null sink, which needs no
  admin rights — `pactl load-module module-null-sink sink_name=cai_sink` then
  `--backend pulse --device cai_sink.monitor`.

### Verification
Gate C's audio hop is reported **BLOCKED**. `scripts/inspect_audio.sh` exits
non-zero and prints this remediation; `scripts/e2e_test.sh --tier full-e2e` exits `3`
with the reason. After installing a virtual device, the same command becomes the
verification.

### Prevention
* `scripts/doctor.sh` treats "microphones only" as a blocking issue.
* `cai doctor` warns with the same wording.
* Do not record with a microphone-class input and call it a system-audio capture:
  GOTCHA-AUDIO-06 shows it *can* produce a QC-passing file that contains the room,
  not the video.

### References
`scripts/inspect_audio.sh`, `docs/LINUX_AUDIO_SETUP.md`, `src/creator_audio_intel/recorder.py`

---

## GOTCHA-AUDIO-06 — a QC-passing capture of the *wrong* audio

### Severity
P1

### Status
Mitigated

### Symptoms
The first real end-to-end run reported `completed: 1` with `qc_ok=1, qc_reason='ok'`,
RMS 300 — and the audio was the room, not the video. The system output volume was
`0`, so Chrome was playing silently and the microphone captured ambient noise.

### Reproduction
```bash
osascript -e 'output volume of (get volume settings)'   # 0
.venv/bin/cai --db data/e2e.sqlite3 run ... --limit 1 --rights-ok
sqlite3 data/e2e.sqlite3 "select rms, qc_ok, qc_reason from captures;"
# 300.28|1|ok     <-- passes QC, contains no video audio
```

### Expected Behavior
QC should not pass audio that does not contain the video.

### Actual Behavior
RMS alone cannot know what the audio *is*; a noisy room is indistinguishable from a
quiet recording without listening or transcribing.

### Root Cause
Passive QC cannot establish content identity. This is inherent, not a bug in the
threshold.

### Diagnosis
`ffmpeg -af volumedetect` on the capture gave `mean_volume: -40.8 dB`,
`max_volume: -19.7 dB`, and its transcript was hallucinated repetition.

### Fix
Mitigated on three levels:
1. **Detected**: `scripts/inspect_audio.sh --device X` records and analyses before any
   pipeline run, and exits non-zero when the capture is unusable.
2. **Documented**: the volume precondition (system output > 0, correct device) is
   stated in `docs/LINUX_AUDIO_SETUP.md`, and `cai doctor` refuses to call a
   microphone-only setup ready.
3. **Now downstream-detectable**: the ASR stage raises
   `Whisper returned no speech segments` for such a capture, so a full pipeline run
   ends with an explicit `stage_errors` entry instead of a silent success.

### Verification
After setting the output volume to 65 and re-running, the capture's transcript was
still unintelligible — which correctly routed to GOTCHA-AUDIO-05 rather than being
reported as a success. Before the fix the same run reported `completed: 1` with no
error at all.

### Prevention
Never treat "QC passed" as "the right audio was recorded". Confirm by listening and
by transcript, which the acceptance gates require.

### References
`scripts/inspect_audio.sh`, `docs/TEST_REPORT.md`, `src/creator_audio_intel/asr.py`

---

## GOTCHA-ASR-01 — faster-whisper 1.2.1 is incompatible with PyAV 19

### Severity
P1

### Status
Fixed

### Symptoms
The very first real transcription failed, before any audio was read:

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

### Reproduction
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[asr]'          # pulls av 19.0.2
python -c "
from creator_audio_intel.asr import transcribe_audio
transcribe_audio('data/fixtures/01_macro.wav')
"
# File ".../faster_whisper/audio.py", line 46, in decode_audio
#   with av.open(input_file, mode="r", metadata_errors="ignore") as container:
# TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

### Expected Behavior
Transcription runs after `pip install -e '.[asr]'`.

### Actual Behavior
Import-time-clean install, runtime crash on the first file.

### Root Cause
PyAV 19 removed/renamed the `metadata_errors` keyword that faster-whisper 1.2.1
still passes to `av.open()`. The `asr` extra placed no upper bound on `av`, so a
fresh install always resolved to the broken combination.

### Diagnosis
```bash
pip show av | head -2        # Version: 19.0.2
pip install 'av<19' && python -c "import av; print(av.__version__)"   # 18.1.0
# retry: transcription succeeds
```

### Fix
`pyproject.toml`: `asr = ["faster-whisper>=1.1,<2", "av>=14,<19"]`, with the failure
mode recorded in a comment so nobody "helpfully" removes the pin.

### Verification
```bash
pip install -e '.[asr]' && python scripts/asr_benchmark.py \
  data/fixtures/01_macro.wav --truth scripts/fixture_texts/01_macro.txt
# model=small version=1.2.1 device=cpu/int8 ; CER = 1.11%
```
`scripts/e2e_test.sh --tier offline` runs this on every invocation.

### Prevention
The pin is in the dependency spec, and the offline tier exercises the real model, so
a future incompatible resolution fails in CI-ish local runs rather than in a
deployment.

### References
`pyproject.toml`, `src/creator_audio_intel/asr.py`

---

## GOTCHA-ASR-02 — Whisper emits Traditional Chinese for Mandarin audio

### Severity
P2

### Status
Mitigated

### Symptoms
A CER measurement of **30.61 %** on a clip whose transcription was, by ear, almost
perfect. The hypothesis used Traditional characters (`我們`, `數據`, `生產總值`,
`宏觀`) while the reference was Simplified; and three domain terms
(`国内生产总值`, `社会消费品零售总额`, `工业增加值`) were reported as *missing* even
though every character was correct.

### Reproduction
```bash
python scripts/asr_benchmark.py data/fixtures/01_macro.wav --truth scripts/fixture_texts/01_macro.txt
# CER = 60/196 = 30.61%     <-- before script folding
# domain term check: 国内生产总值 [MISS] 社会消费品零售总额 [MISS] 工业增加值 [MISS]
```

### Expected Behavior
CER should reflect *recognition* errors only.

### Actual Behavior
Script choice alone accounted for almost the entire error count. It also means a
stored transcript may not contain the Simplified substring an operator searches for.

### Root Cause
Whisper's decoder may output either script for `language='zh'`; the benchmark
compared raw strings.

### Diagnosis
`zhconv.convert(hypothesis, 'zh-cn')` made the three "missing" terms match and dropped
CER to **1.11 %** (2 real edits: an inserted `在` and 企→起 in `企稳迹象`).

### Fix
* Benchmark: fold both sides to Simplified before measuring, and *separately* report
  how many terms matched only after folding, so the script issue is visible rather
  than hidden.
* Runtime: the raw ASR text is deliberately **not** rewritten — the specification
  requires the original transcript to be preserved and the LLM must quote it
  verbatim. The consequence (searching `房地产` will not match a Traditional
  transcript) is documented. `db.search` does literal substring matching only.

### Verification
```bash
python scripts/asr_benchmark.py data/fixtures/01_macro.wav --truth scripts/fixture_texts/01_macro.txt
# WARNING: ASR emitted Traditional Chinese; simplified form: ...
# CER = 2/180 = 1.11%
# NOTE: 3 term(s) missing ONLY because the ASR used Traditional characters: [...]
```
Live evidence: the extracted claim quotes are Traditional and still validate, because
verbatim matching is against the stored transcript.

### Prevention
The benchmark warns explicitly. Adding Simplified folding to storage is a product
decision (it would alter the record) and is left open.

### References
`scripts/asr_benchmark.py` (`to_simplified`, `normalize`)

---

## GOTCHA-ASR-03 — spoken Chinese numerals are rewritten as digits and percent signs

### Severity
P2

### Status
Fixed

### Symptoms
Numbers reported as missing although they were transcribed correctly:
`百分之四点九` → `4.9%`, `二零二五` → `2025`. A naive "is the number present?" check
reported `[MISS] 4.9`.

### Reproduction
```bash
python scripts/asr_benchmark.py data/fixtures/01_macro.wav --truth scripts/fixture_texts/01_macro.txt | sed -n '/number spot/,$p'
# [MISS] 49  [MISS] 33  ...    (before normalisation)
```

### Expected Behavior
A numeric spot check should compare like with like.

### Actual Behavior
Reference numerals were Chinese words, hypothesis numerals were ASCII digits.

### Root Cause
Whisper normalises numerals; the reference was written the way it is spoken.

### Diagnosis
`'二零二五' in hypothesis` → False, but `'2025' in hypothesis` → True.

### Fix
Normalise both sides to ASCII: `百分之X点Y` → `X.Y`, `百分之X` → `X`, digit runs of
length ≥2 → digits (so `二零二五` → `2025`), and strip `%`.

**Two wrong first attempts are worth recording**, because both are easy traps and are
now pinned by the code comments:
* a blanket `零 → 0` translation corrupts **零售** ("retail", literally 零 + 售) and
  broke the domain-term check for `社会消费品零售总额`;
* a blanket `点 → .` translation corrupts 观点 / 重点.

An off-by-one in the digit map (including `〇` in the table without mapping it to `0`)
shifted every digit — `二零二五` became `3036`.

### Verification
```bash
python scripts/asr_benchmark.py data/fixtures/01_macro.wav --truth scripts/fixture_texts/01_macro.txt
# number spot check (normalized): [OK] 2025 [OK] 4.9 [OK] 3.3 [OK] 5.8 [OK] 7.2
# domain term check: all 5 [OK]
```

### Prevention
Normalisation is scoped to explicit numeric contexts, and the two corrupting
counter-examples are asserted in the module's comments and exercised by
`scripts/e2e_test.sh --tier offline`.

**Known limitation:** multiplicative numerals (`百分之三十` → `3十`) are not
converted; the benchmark prints this as a limitation rather than a recognition error.

### References
`scripts/asr_benchmark.py` (`_PCT_DECIMAL`, `_CN_RUN`, `normalize_numbers`)

---

## GOTCHA-LLM-01 — reasoning tokens silently truncate the structured output

### Severity
P1

### Status
Fixed

### Environment
- LLM: `iagent/standard` (`https://iagent.iauto.com/v1`), OpenAI-compatible

### Symptoms
`ERROR: LLM request failed after 1 attempts: completion truncated
(finish_reason=length)` for a 48-second transcript.

### Reproduction
```bash
.venv/bin/cai --db data/gateB.sqlite3 extract --video-row-id 1 --llm-model iagent/standard
# ERROR: LLM request failed after 1 attempts: completion truncated (finish_reason=length)
```

### Expected Behavior
A 48 s transcript's extraction should fit comfortably in the token budget.

### Actual Behavior
The completion was cut mid-JSON. The endpoint returns a hidden `reasoning` field
alongside `content`, and those tokens count against `max_tokens` — a 136-token
prompt already spent 502 reasoning tokens.

### Root Cause
`max_tokens` defaulted to 4096 for a schema-constrained extraction from a
reasoning-emitting model; and `_extract_chunk` called `chat_json(..., max_attempts=1)`,
so a transport-class failure had no retry at all.

### Diagnosis
```bash
python - <<'PY'
import json, os, urllib.request
# one tiny request, observe usage:
# {"prompt_tokens": 136, "completion_tokens": 549, "prompt_tokens_details": null,
#  "completion_tokens_details": {"reasoning_tokens": 502}}
PY
```

### Fix
`DEFAULT_MAX_TOKENS = 8192` for structured extraction (the model advertises 16384),
and `_extract_chunk` now catches `LLMError` and retries the whole attempt instead of
propagating after one shot.

### Verification
```bash
.venv/bin/cai --db data/gateB.sqlite3 extract --video-row-id 1 --llm-model iagent/standard
# LLM run id 1
```
Regression test: `test_gotcha_llm_truncated_completion_is_retried`

### Prevention
A truncated completion is treated as retryable, not as a final answer. If a future
model spends more reasoning budget, the failure is a retryable error, not a
silently truncated record.

### References
`src/creator_audio_intel/llm.py` (`DEFAULT_MAX_TOKENS`, `_extract_chunk`)

---

## GOTCHA-LLM-02 — the JSON schema forces forecast fields onto every claim, and a temperature-0 rejection repeats forever

### Severity
P1

### Status
Fixed

### Symptoms
```
ERROR: Extraction failed validation after 3 attempts:
LLM output rejected by evidence validator: Non-forecast claim contains prediction data
```
Three attempts, identical failure.

### Reproduction
```bash
.venv/bin/cai --db data/gateB.sqlite3 extract --video-row-id 1 --llm-model iagent/standard
```

### Expected Behavior
Either a valid extraction, or a retry that actually changes the outcome.

### Actual Behavior
The strict schema (`additionalProperties: false`, all fields `required`) forces
`forecast.{metric,direction,horizon,conditions}` to exist on *every* claim, so the
model fills them even for `kind='opinion'`. The validator correctly rejects that, but
at `temperature=0` the retry produced the same bytes.

### Root Cause
A schema/prompt/validator three-way tension: the schema demands the fields, the
validator demands they be empty for non-forecasts, and the prompt did not say so
explicitly; the retry then re-sent an identical request.

### Diagnosis
Inspected the rejected payload: a `fact`/`opinion` claim carried a non-empty
`forecast.conditions`.

### Fix
1. The system prompt now states the rule explicitly: for `kind != forecast` all four
   fields must be the empty string, and that this is a hard schema requirement.
2. An initial attempt therefore succeeds; the empty-string rule is honoured.
3. When an attempt *is* rejected, the reason is fed back verbatim in a corrective
   system message (`CORRECTION_PROMPT`) naming the three invariants, so the next
   attempt is materially different rather than a byte-identical replay.

### Verification
```bash
.venv/bin/cai --db data/gateB.sqlite3 extract --video-row-id 1 --llm-model iagent/standard
# LLM run id 1
.venv/bin/cai --db data/gateB.sqlite3 evidence --video-row-id 1 | \
  python -c "import json,sys; d=json.load(sys.stdin); print(len(d['claims']), 'claims')"
# 6 claims, 6/6 quotes verbatim, forecast fields populated only for kind=forecast
```
Regression tests:
`test_gotcha_llm_retry_prompt_carries_the_rejection_reason`,
`test_gotcha_llm_error_message_hides_api_key`

### Prevention
Validator rules must be stated in the prompt that generates the payload. Any new
validator rule needs a matching prompt sentence and a regression test — otherwise
retries burn quota without ever converging.

### References
`src/creator_audio_intel/llm.py` (`SYSTEM_PROMPT`, `CORRECTION_PROMPT`,
`validate_output`, `_extract_chunk`)

---

## GOTCHA-LLM-03 — the provider changed from local Ollama to an OpenAI-compatible endpoint

### Severity
P2

### Status
Fixed

### Symptoms
The previous implementation required a locally installed Ollama model
(`--llm-provider ollama`, `POST /api/chat` with a `format` field) and validated the
endpoint as *must be localhost*. Running it here would have needed a downloaded
model; the operator specified `iagent/standard` instead.

### Expected Behavior
Extraction through the configured `iagent/standard` endpoint, with the same evidence
guarantees.

### Actual Behavior
`extract_ollama()` and a localhost-only URL check.

### Root Cause
Design decision, superseded by operator instruction.

### Fix
* `extract_claims()` posts to `<base>/chat/completions` with
  `response_format={"type":"json_schema","json_schema":{"strict":true,...}}`.
* Configuration: `CAI_LLM_BASE_URL` / `CAI_LLM_API_KEY` / `CAI_LLM_MODEL`;
  falls back to `IAUTO_BASE_URL` / `IAUTO_API_KEY` / `IAUTO_MODEL`.
* URL policy: HTTPS, or HTTP only on loopback; embedded credentials rejected.
* `PROMPT_VERSION` bumped v1 → v2 so old rows are not misread as new-prompt results.
* `llm_runs.provider` now records the endpoint host (`iagent.iauto.com`) so provenance
  survives a future provider change.

### Verification
```bash
python -c "
from creator_audio_intel.llm import healthcheck; print(healthcheck())
"
# {'base_url': 'https://iagent.iauto.com/v1', 'model': 'iagent/standard',
#  'latency_seconds': ..., 'reply': '{"ok": true}'}
sqlite3 data/gateB.sqlite3 "select provider, model, prompt_version from llm_runs;"
# iagent.iauto.com|iagent/standard|v2
```
Regression tests: `test_gotcha_llm_base_url_security`, `test_gotcha_llm_config_precedence`

### Prevention
Ollama is no longer referenced by the CLI, README or `.env.example`.
`bootstrap_linux.sh` notes that an installed Ollama is now unused.

### References
`src/creator_audio_intel/llm.py`, `.env.example`, `README.md`

---

## GOTCHA-SQLITE-01 — "video identity" ignored the platform

### Severity
P1

### Status
Fixed

### Symptoms
Adding a second source platform made the same numeric id ambiguous: `put_video`
hard-coded `platform='douyin'` and `creator()` did not store a platform, while
`video_id_from_url`/`validate_profile_url` only accepted Douyin hosts. There was no
way to verify the pipeline against a local site without either faking Douyin URLs or
colliding with a real Douyin row.

### Reproduction
```python
db.put_video(db.creator('http://127.0.0.1:8099/user/x'), VideoCandidate('9000000000000000000', ...))
# -> stored as platform='douyin', profile rejected by validate_profile_url
```

### Expected Behavior
Identity is `(platform, platform_video_id)` — which the schema already declared.

### Actual Behavior
The schema said so; the code did not.

### Root Cause
`platform` was hard-coded in two methods and absent from the URL validators.

### Diagnosis
`sqlite3 ... "select platform, platform_video_id from videos"` showed every row as
`douyin` regardless of origin.

### Fix
`platform` is threaded through `security.validate_profile_url`,
`security.video_id_from_url`, `db.creator`, `db.put_video`,
`ingest.import_wav`, `pipeline.discover/run_profile/capture_video`, and
`browser.discover/open/wait_for_end`; `--platform {douyin,fixture}` on the CLI. The
audio path gained the platform segment
(`data/audio/<platform>/<video_id>/`) so captures cannot overwrite each other.

### Verification
```bash
python -m pytest tests/test_regressions_db.py -q
# test_gotcha_platform_is_part_of_the_same_video_id_space  -> same numeric id, two distinct rows
# test_gotcha_fixture_urls_are_not_accepted_as_douyin
sqlite3 data/e2e.sqlite3 "select platform, platform_video_id, status from videos;"
# fixture|9000000000000000000|...
```

### Prevention
By using a distinct platform *and* host for verification data, a fixture result can
never be reported as a Douyin result. `db.stats_by_platform()` exposes the split.

### References
`src/creator_audio_intel/security.py`, `db.py`, `ingest.py`, `pipeline.py`

---

## GOTCHA-OPS-01 — the dedicated Chrome died mid-session with no supervisor

### Severity
P2

### Status
Mitigated

### Symptoms
A run failed with:

```
browser-harness: fatal: BU_CDP_URL=http://127.0.0.1:9222 unreachable after 30s:
<urlopen error [Errno 61] Connection refused>
-- is the dedicated automation Chrome running? Launch it with
--remote-debugging-port=<port> --user-data-dir=<dedicated dir>
```

### Reproduction
Launch Chrome, wait, and check: `curl http://127.0.0.1:9222/json/version`. After the
browser window is closed (accidentally or by the OS), the endpoint is gone while the
run is still in flight.

### Expected Behavior
A headerless automation browser should not be closable by accident mid-run.

### Actual Behavior
Nothing restarts it and nothing detects the loss before the next CDP call.

### Root Cause
No process supervision for the automation browser (and no launchd/systemd unit for
it here — see GOTCHA-ENV-02).

### Fix
Mitigated: `scripts/launch_chrome_cdp.sh` creates the dedicated profile, binds CDP to
loopback, waits until `/json/version` answers, and reuses an existing endpoint instead
of starting a second browser. `scripts/doctor.sh` checks CDP reachability before
anything else. The local Chrome log also showed the earlier instance dying after a
burst of TLS handshake errors, i.e. after the Douyin attempt — so keep the discovery
step away from unreachable hosts (GOTCHA-ENV-01).

### Verification
```bash
scripts/launch_chrome_cdp.sh --quiet
# CDP ready on 127.0.0.1:9222
BU_CDP_URL=http://127.0.0.1:9222 browser-harness <<'PY'
print(page_info())
PY
# {'url': 'about:blank', ...}
```

### Prevention
`scripts/e2e_test.sh` checks CDP before the browser tiers and reports `SKIP` with this
reason rather than failing obscurely. A `launchd`/`systemd` unit for the automation
browser is the proper long-term answer and is provided untested in
`docs/LINUX_DEPLOYMENT.md`.

### References
`scripts/launch_chrome_cdp.sh`, `docs/LINUX_DEPLOYMENT.md`

---

## GOTCHA-SEC-01 — a CDP port is unauthenticated remote control of the browser

### Severity
P1

### Status
Mitigated

### Symptoms
Not a defect that occurred; a constraint that must hold. `--remote-debugging-port`
has no authentication: anyone who can reach the port can read cookies, navigate, and
execute JavaScript in the profile.

### Reproduction (demonstrates the exposure, not run against a public bind)
```bash
curl -s http://127.0.0.1:9222/json/version   # full browser control: no auth
lsof -nP -iTCP:9222 -sTCP:LISTEN
# Google ... TCP 127.0.0.1:9222 (LISTEN)     <-- loopback only
```

### Expected Behavior
CDP is reachable only from the local host, on a dedicated profile that holds no
personal sessions.

### Actual Behavior
Correct as configured, but nothing in the original code prevented a public bind.

### Root Cause
Ollama's original localhost-only restriction was the only such guard; nothing covered
Chrome, and the port was operator-supplied.

### Fix
* `scripts/launch_chrome_cdp.sh` passes `--remote-debugging-address=127.0.0.1` and
  **refuses to reuse a non-loopback endpoint**; the bind address is not a flag.
* A dedicated `--user-data-dir` (`~/.cai/chrome-profile`) keeps the operator's own
  cookies out of automation reach.
* The LLM client enforces HTTPS (or loopback HTTP) and rejects credentials in the URL.
* Apache-style `.gitignore` rules keep the profile and all captured media out of Git.

### Verification
```bash
lsof -nP -iTCP:9222 -sTCP:LISTEN | grep LISTEN
# TCP 127.0.0.1:9222 (LISTEN)
git check-ignore .cai/chrome-profile/x data/fixtures/01_macro.mp4 .env
# all three reported as ignored
python -m pytest tests/test_regressions_llm.py -q   # URL policy enforced
```

### Prevention
Never bind CDP to `0.0.0.0`, never expose the SQLite file or an admin endpoint over the
network, and never commit the automation profile.

### References
`scripts/launch_chrome_cdp.sh`, `src/creator_audio_intel/llm.py`, `.gitignore`

---

## GOTCHA-SEC-02 — an API key on the command line leaks into history and process listings

### Severity
P2

### Status
Fixed

### Symptoms
Adding a `--llm-api-key` flag was considered and rejected: `ps` and shell history
would record the secret.

### Expected Behavior
The key comes from the environment or a file the operator controls.

### Fix
There is deliberately no key flag. The key is read from `CAI_LLM_API_KEY` /
`IAUTO_API_KEY` only, and never echoed: `_post_json` puts it in an `Authorization`
header, and `chat_json` error strings contain only transport detail. A regression test
asserts the key cannot appear in an error message.

### Verification
```bash
python -m pytest tests/test_regressions_llm.py::test_gotcha_llm_error_message_hides_api_key -q
# 1 passed
grep -rn 'llm-api-key\|api_key=' src/creator_audio_intel/cli.py || echo "no key flag, as intended"
```

### Prevention
`grep -i 'key' .env.example` shows only a comment, never a value.

### References
`src/creator_audio_intel/llm.py`, `src/creator_audio_intel/cli.py`

---

## Potential / Not Reproduced

Risks acknowledged by the design and by the specification but **not** observed here.
They are listed so that a future operator does not mistake their absence from the
"real" entries above for a guarantee.

| ID | Risk | Why not observed | Mitigation in place |
|---|---|---|---|
| POTENTIAL-01 | SQLite lock contention (`database is locked`) under concurrent writers | The pipeline is single-writer by construction; `RunLock` was never contended on a real run | `busy_timeout=10000`, WAL, `RunLock`. `scripts/verify_sqlite.py` reports `journal_mode` |
| POTENTIAL-02 | Disk full during a long capture, leaving a truncated WAV | Disk was at 3 % of 926 GB | Recorder writes `.partial.wav` and renames only after a clean exit; `ffmpeg` exit code is checked; `reap_orphans` cleans pidfiles |
| POTENTIAL-03 | WAL growth on a long-running service | No long-running service was started (GOTCHA-ENV-02) | Documented; `PRAGMA wal_checkpoint` not automated |
| POTENTIAL-04 | Douyin DOM change breaking discovery selectors | Douyin is unreachable (GOTCHA-ENV-01) | Zero results are classified (`DOM_CHANGED` / `EMPTY_PROFILE` / ...), never returned as success |
| POTENTIAL-05 | Douyin autoplay recommendations contaminating the audio | Douyin is unreachable | `wait_for_end` fails closed on navigation to another video and on player swap |
| POTENTIAL-06 | `systemd --user` unit failing to inherit DISPLAY/audio session | Linux unavailable | Unit file provided untested in `docs/LINUX_DEPLOYMENT.md`; the session-dependency requirement is documented |
| POTENTIAL-07 | Two agents driving the same daemon concurrently | Runs were strictly serial (GOTCHA-BROWSER-08) | Single-writer design; do not add parallel browser steps without a separate `BU_NAME` |
| POTENTIAL-08 | Whisper hallucination on long silence producing invented text | The fixture has continuous speech; only the failed microphone capture hallucinated | `vad_filter=True`; empty transcripts raise instead of being stored; the raw transcript is preserved and the LLM must quote it verbatim |
| POTENTIAL-09 | Forecast correctness being mistaken for verified economic fact | Out of scope by design (PRD) | `claims.verification_status` defaults to `not_evaluated`; docs state the limitation repeatedly |
| POTENTIAL-10 | Creator identity being asserted without proof | Douyin unreachable, so homepage proof could not be obtained | `videos.creator_id` is recorded but the pipeline does not claim verified ownership; `docs/PRD.md` Gate B requires operator verification |

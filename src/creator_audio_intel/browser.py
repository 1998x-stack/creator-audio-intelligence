"""Live browser-harness adapter.

No Douyin private endpoints, downloaded media URLs or anti-bot bypasses.
Site structure and the Chrome CDP connection MUST be validated on the target
machine; every selector here was exercised against a real Chrome instance and
the observed failures are recorded in docs/gotchas.md.

Observed real-browser lessons encoded below:
  * `video.play()` raises NotAllowedError without a user gesture, so playback
    needs a real CDP click at the player box; the click only works if the
    element is actually inside the viewport (a below-the-fold box centre
    silently clicks nothing).
  * `duration` is `null` until `loadedmetadata`; reading it immediately after
    navigation produced false "no suitable player" errors.
  * Re-selecting "the largest visible video" on every poll can switch elements
    mid-recording, so the chosen element is pinned for the whole lifecycle.
  * browser-harness keeps ONE attached tab per daemon, so two concurrent
    invocations interleave their work on the same tab.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import time
from .models import VideoCandidate
from .security import video_id_from_url, validate_profile_url

_PREFIX = 'CAI_OUTPUT_JSON:'

# Set BH_TAB_MARKER=0 to stop browser-harness rewriting page titles.
_TITLE_MARKER = '🐴 '

# Pick the pinned player first, else the largest video that has layout boxes.
# Pinning keeps one element across the whole record lifecycle.
_SEL = ("const _p=()=>{"
        "const t=document.querySelector('video[data-cai-player=\"1\"]');if(t)return t;"
        "const l=Array.from(document.querySelectorAll('video')).filter(v=>{"
        "if(!v.getClientRects().length)return false;"
        "const r=v.getBoundingClientRect();return r.width>100&&r.height>100;"
        "}).sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();"
        "return y.width*y.height-x.width*x.height;});"
        "return l[0]||null;};")

_DISCOVERY_JS = r"""(()=>{
  const links = Array.from(document.querySelectorAll('a[href]'))
    .filter(a=>{const r=a.getBoundingClientRect();return r.width>0&&r.height>0;})
    .map(a=>({url:a.href,title:(a.getAttribute('title')||a.textContent||'').trim().slice(0,250)}))
    .filter(x=>/\/video\/[0-9]{12,25}(?:[/?#]|$)/.test(new URL(x.url).pathname));
  const body = document.body ? (document.body.innerText||'') : '';
  return {
    links: links,
    videoElements: document.querySelectorAll('video').length,
    anchorCount: document.querySelectorAll('a[href]').length,
    videoAnchors: document.querySelectorAll('a[href*="/video/"]').length,
    loginPrompt: /立即登录|登录后|请先登录|扫码登录|verify|验证码|captcha/i.test(body),
    bodySample: body.slice(0,300),
    url: location.href,
    title: document.title
  };
})()"""

_PLAYER_JS = ("(()=>{" + _SEL + r""" const v=_p();
 if(!v)return {found:false,url:location.href,videoElements:document.querySelectorAll('video').length};
 const r=v.getBoundingClientRect();
 return {found:true,url:location.href,
   pinned:document.querySelectorAll('video[data-cai-player="1"]').length===1&&v.hasAttribute('data-cai-player'),
   paused:v.paused,ended:v.ended,currentTime:v.currentTime,
   duration:Number.isFinite(v.duration)?v.duration:null,
   readyState:v.readyState,muted:v.muted,loop:v.loop,volume:v.volume,
   source:(v.currentSrc||v.src||'').slice(0,300),
   box:{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,h:r.height},
   inViewport:r.top>=0&&r.left>=0&&r.bottom<=innerHeight&&r.right<=innerWidth,
   videoElements:document.querySelectorAll('video').length};})()""")

# Pin, unmute, unloop, rewind, and bring the element into the viewport so a
# gesture click can actually land on it.
_PREPARE_JS = ("(()=>{" + _SEL + r""" const v=_p();
 if(!v)return {ok:false,reason:'no video element'};
 Array.from(document.querySelectorAll('video[data-cai-player="1"]'))
   .forEach(o=>{if(o!==v)o.removeAttribute('data-cai-player');});
 v.setAttribute('data-cai-player','1');
 try{v.pause();}catch(e){}
 v.loop=false;v.muted=false;if(v.volume===0)v.volume=1;
 try{v.currentTime=0;}catch(e){return {ok:false,reason:String(e)};}
 try{v.scrollIntoView({block:'center',inline:'center'});}catch(e){}
 const r=v.getBoundingClientRect();
 return {ok:true,time:v.currentTime,readyState:v.readyState,
   duration:Number.isFinite(v.duration)?v.duration:null,
   inViewport:r.top>=0&&r.left>=0&&r.bottom<=innerHeight&&r.right<=innerWidth,
   box:{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width,h:r.height}};})()""")

# browser-harness IPC has a 5s default response budget, so long awaits inside JS
# time out. Metadata is polled from Python with short, non-blocking reads.
_METADATA_JS = ("(()=>{" + _SEL + r""" const v=_p();
 if(!v)return {ok:false,reason:'no video element'};
 if(Number.isFinite(v.duration)&&v.duration>0)
   return {ok:true,duration:v.duration,readyState:v.readyState};
 // Force a metadata fetch for players that do not preload.
 try{if(v.preload!=='auto'){v.preload='auto';v.load();}}catch(e){}
 return {ok:false,readyState:v.readyState,duration:null,networkState:v.networkState};})()""")

_PLAY_JS = ("(async()=>{" + _SEL + r""" const v=_p();
 if(!v)return {ok:false,reason:'no video element'};
 try{await v.play();}catch(e){return {ok:false,reason:String(e),paused:v.paused,muted:v.muted};}
 return {ok:!v.paused,paused:v.paused,muted:v.muted,currentTime:v.currentTime};})()""")

_FOCUS_JS = "(()=>({focused:document.hasFocus(),visibility:document.visibilityState}))()"

_STATUS_JS = ("(()=>{" + _SEL + r""" const v=_p();
 return {playerPinned:!!document.querySelector('video[data-cai-player="1"]'),
   hasPlayer:!!v,
   paused:v?v.paused:null,
   currentTime:v?v.currentTime:null,
   ended:v?v.ended:null,
   duration:v&&Number.isFinite(v.duration)?v.duration:null,
   readyState:v?v.readyState:null,
   focused:document.hasFocus(),visibility:document.visibilityState,
   body:(document.body?document.body.innerText:'').slice(0,300),
   url:location.href,title:document.title};})()""")


class BrowserError(RuntimeError):
    pass


class DiscoveryError(BrowserError):
    """Discovery returned nothing usable; carries a machine-readable code."""

    CODES = ('EMPTY_PROFILE', 'DISCOVERY_FAILED', 'LOGIN_REQUIRED',
             'ACCESS_DENIED', 'DOM_CHANGED', 'TIMEOUT')

    def __init__(self, code: str, detail: str = ''):
        if code not in self.CODES:
            raise ValueError(f'Unknown discovery code {code}')
        self.code = code
        self.detail = detail
        super().__init__(f'No visible video links found [{code}]'
                         + (f': {detail}' if detail else ''))


def _clean_title(title) -> str:
    if not isinstance(title, str):
        return ''
    return title[len(_TITLE_MARKER):] if title.startswith(_TITLE_MARKER) else title


class BrowserHarness:
    def __init__(self, timeout=60):
        self.timeout = timeout
        self.tab_open = False
        self.last_discovery_status: str | None = None
        self.scroll_blocked = False

    def _run(self, script: str):
        if not shutil.which('browser-harness'):
            raise BrowserError('browser-harness executable not found')
        try:
            out = subprocess.run(['browser-harness'], input=script,
                                 capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BrowserError('browser-harness timed out') from exc
        if out.returncode:
            raise BrowserError((out.stderr or out.stdout)[-500:])
        for line in reversed(out.stdout.splitlines()):
            if _PREFIX in line:
                try:
                    return json.loads(line.split(_PREFIX, 1)[1])
                except ValueError as exc:
                    raise BrowserError('Invalid browser-harness JSON') from exc
        raise BrowserError('No structured result from browser-harness')

    def _eval(self, js_expression: str):
        script = ('import json\n'
                  'result=js(' + json.dumps(js_expression) + ')\n'
                  'print(' + json.dumps(_PREFIX) + '+json.dumps(result,ensure_ascii=False))\n')
        return self._run(script)

    def open(self, url: str):
        """Navigate and wait until the target document is actually the one loaded.

        `goto_url()` is `Page.navigate`, which returns before the navigation
        happens, and `wait_for_load()` can then observe the *previous* document
        (already `complete`) and return immediately. That race made a later
        player check read the old page. So poll for the target URL *and*
        `document.readyState === 'complete'` before reporting success.
        """
        target = url.split('#')[0]
        script = ('import json, time\n'
                  'target=' + json.dumps(target) + '\n'
                  + ('new_tab(target)\n' if not self.tab_open else 'goto_url(target)\n')
                  + 'wait_for_load()\n'
                  'deadline=time.time()+25\n'
                  'info=page_info()\n'
                  'while time.time()<deadline:\n'
                  '    try:\n'
                  '        info=page_info()\n'
                  '        ready=js("document.readyState")\n'
                  '    except Exception:\n'
                  '        ready=None\n'
                  '    if str(info.get("url","")).split("#")[0]==target and ready=="complete":\n'
                  '        break\n'
                  '    time.sleep(0.25)\n'
                  + 'print(' + json.dumps(_PREFIX) + '+json.dumps(info,ensure_ascii=False))\n')
        attempt = 0
        while True:
            try:
                result = self._run(script)
                break
            except BrowserError as exc:
                # A 5s IPC budget can be exceeded by a cold navigation.
                attempt += 1
                if attempt >= 2 or 'timed out' not in str(exc).lower():
                    raise
                self.tab_open = True
                script = script.replace('new_tab(target)', 'goto_url(target)')
        self.tab_open = True
        if isinstance(result, dict):
            result['title'] = _clean_title(result.get('title'))
            result['requested_url'] = target
        return result

    # ------------------------------------------------------------------ discovery
    def page_diagnostics(self) -> dict:
        return self._eval(_DISCOVERY_JS)

    def _classify_empty(self, diag: dict) -> DiscoveryError:
        """Map an empty result onto the required failure taxonomy.

        Order matters: a network/login wall must not be reported as an empty
        profile merely because it also lacks anchors.
        """
        url = str(diag.get('url') or '')
        body = str(diag.get('bodySample') or '')
        if url.startswith('chrome-error://'):
            return DiscoveryError('ACCESS_DENIED', body.splitlines()[0][:160] if body else 'navigation error page')
        if diag.get('loginPrompt'):
            return DiscoveryError('LOGIN_REQUIRED', 'page shows a login/verification prompt')
        if not diag.get('anchorCount') and not diag.get('videoElements'):
            return DiscoveryError('DOM_CHANGED',
                                  'page has no anchors, no video elements; markup may have changed')
        if diag.get('videoAnchors'):
            return DiscoveryError('DOM_CHANGED',
                                  f"{diag['videoAnchors']} /video/ anchors present but none had a usable id")
        return DiscoveryError('EMPTY_PROFILE', 'page loaded but exposed no video links')

    def _open(self, url: str):
        """open(), translating a harness timeout into the discovery taxonomy."""
        try:
            return self.open(url)
        except BrowserError as exc:
            if 'timed out' in str(exc).lower():
                raise DiscoveryError('TIMEOUT', str(exc)) from exc
            raise

    def _scroll_once(self) -> bool:
        """Wheel-scroll the page, activating the tab if it is throttled.

        browser-harness documents that a timed-out `scroll()` on a background tab
        means the page needs to be visible. We activate and retry exactly once,
        then give up on scrolling (discovery still reports whatever it saw) rather
        than inventing a `Runtime.evaluate` scroll replacement.
        """
        script = ('import json\nscroll(500,450,dy=650)\n'
                  'print(' + json.dumps(_PREFIX) + '+json.dumps({"ok":True}))\n')
        try:
            self._run(script)
            return True
        except BrowserError:
            pass
        try:
            self._run('import json\nactivate_tab(current_tab())\n'
                      'print(' + json.dumps(_PREFIX) + '+json.dumps({"activated":True}))\n')
            self._run(script)
            return True
        except BrowserError:
            return False

    def discover(self, profile: str, scroll_rounds: int = 4,
                 platform: str = 'douyin') -> list[VideoCandidate]:
        validate_profile_url(profile, platform)
        self._open(profile)
        seen: dict[str, VideoCandidate] = {}
        anchors_ever = 0
        self.scroll_blocked = False
        diag: dict = {}
        for i in range(scroll_rounds + 1):
            try:
                diag = self.page_diagnostics()
            except BrowserError as exc:
                if 'timed out' in str(exc).lower():
                    raise DiscoveryError('TIMEOUT', str(exc)) from exc
                raise
            if not isinstance(diag, dict) or 'links' not in diag:
                raise DiscoveryError('DISCOVERY_FAILED', 'discovery script returned no structure')
            anchors_ever += int(diag.get('anchorCount') or 0)
            for item in diag.get('links') or []:
                try:
                    vid = video_id_from_url(item['url'], platform)
                except (KeyError, TypeError, ValueError):
                    continue
                if vid not in seen:
                    seen[vid] = VideoCandidate(vid, item['url'], _clean_title(item.get('title', '')))
            if i < scroll_rounds:
                if not self._scroll_once():
                    self.scroll_blocked = True
                    break
                time.sleep(1.0)
        if seen:
            self.last_discovery_status = 'OK'
            return list(seen.values())
        if not diag:
            self.last_discovery_status = 'DISCOVERY_FAILED'
            raise DiscoveryError('DISCOVERY_FAILED', 'no diagnostics were collected')
        error = self._classify_empty(diag)
        # Keep the anchor count in the detail for operators triaging the page.
        error.detail = f'{error.detail} (anchors={anchors_ever}, url={diag.get("url")})'
        error.args = (f'No visible video links found [{error.code}]: {error.detail}',)
        self.last_discovery_status = error.code
        raise error

    # ------------------------------------------------------------------- player
    def inspect_player(self):
        return self._eval(_PLAYER_JS)

    def prepare_player(self):
        """Pin, unmute, rewind and make the player gesture-clickable.

        A background tab reports `document.hasFocus() === false`, and Chrome then
        ignores the autoplay gesture click. Activating the harness' own tab is
        the documented remedy; it is only done when focus is actually missing.
        """
        result = self._eval(_PREPARE_JS)
        if not isinstance(result, dict) or not result.get('ok'):
            return result
        focus = self._eval(_FOCUS_JS)
        result['focused'] = bool(focus.get('focused'))
        if not result['focused']:
            try:
                self._run('import json\nactivate_tab(current_tab())\n'
                          'print(' + json.dumps(_PREFIX) + '+json.dumps({"activated":True}))\n')
                time.sleep(0.4)
                focus = self._eval(_FOCUS_JS)
                result['focused'] = bool(focus.get('focused'))
                result['activated_tab'] = True
            except BrowserError:
                result['activated_tab'] = False
        return result

    def wait_for_metadata(self, timeout=20.0, poll_interval=0.75) -> dict:
        """Poll (from Python, not inside JS) until duration is finite.

        A JS-side `await` would blow the 5s browser-harness IPC budget.
        """
        deadline = time.monotonic() + timeout
        last: dict = {'ok': False, 'reason': 'no result'}
        while True:
            last = self._eval(_METADATA_JS)
            if isinstance(last, dict) and last.get('ok'):
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(poll_interval)

    def player_status(self) -> dict:
        return self._eval(_STATUS_JS)

    def play(self):
        result = self._eval(_PLAY_JS)
        if isinstance(result, dict) and result.get('ok'):
            return result
        # autoplay is gesture-gated; click the pinned player centre if it is
        # actually inside the viewport. No CAPTCHA/login bypass is attempted.
        prep = self.prepare_player()
        if not prep.get('ok') or not prep.get('inViewport'):
            return result or {'ok': False, 'reason': 'player not in viewport for gesture'}
        box = prep['box']
        x = min(max(round(box['x'], 2), 1), 100000)
        y = min(max(round(box['y'], 2), 1), 100000)
        self._run('import json\nclick_at_xy(' + str(x) + ',' + str(y) + ')\n'
                  'print(' + json.dumps(_PREFIX) + '+json.dumps({"clicked":True}))\n')
        time.sleep(0.5)
        state = self._eval(_PLAY_JS)
        return state

    def wait_for_end(self, expected_url: str, maximum_seconds: float, poll_interval=1.5,
                     require_pinned=True, platform: str = 'douyin'):
        expected_id = video_id_from_url(expected_url, platform)
        deadline = time.monotonic() + maximum_seconds
        last = None
        last_advance = time.monotonic()
        started = False
        while time.monotonic() < deadline:
            state = self.inspect_player()
            if not state.get('found'):
                raise BrowserError('Player disappeared')
            if require_pinned and not state.get('pinned'):
                # The element chosen before recording is gone: the site swapped
                # players, so the recorded audio may no longer be this video.
                raise BrowserError('Pinned player element disappeared (player swapped mid-recording)')
            try:
                actual_id = video_id_from_url(state.get('url', ''), platform)
            except ValueError:
                raise BrowserError('Unexpected navigation while recording: '
                                   + str(state.get('url'))[:200])
            if actual_id != expected_id:
                raise BrowserError('Navigation to another video while recording')
            if state.get('ended'):
                return state
            cur = float(state.get('currentTime') or 0)
            if cur > 0:
                started = True
            if last is not None and cur < last - 1.0:
                raise BrowserError('Video replay/seek during recording; needs review')
            if last is None or cur > last + 0.1:
                last_advance = time.monotonic()
            if started and time.monotonic() - last_advance > 18:
                raise BrowserError('Player stalled for >18s')
            last = cur
            time.sleep(poll_interval)
        raise BrowserError('Playback exceeded bounded timeout')

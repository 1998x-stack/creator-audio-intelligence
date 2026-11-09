"""Experimental live browser-harness adapter.

No Douyin private endpoints, downloaded media URLs or anti-bot bypasses.
Site structure and Chrome CDP connection MUST be validated in the target machine.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import time
from .models import VideoCandidate
from .security import video_id_from_url, validate_profile_url

_PREFIX = 'CAI_OUTPUT_JSON:'

# Read only explicitly visible HTML links. Dynamic click-only cards require a
# separately tested site-specific adapter; do not claim historical completeness.
_DISCOVERY_JS = r"""(()=>{
  return Array.from(document.querySelectorAll('a[href]'))
    .filter(a=>{const r=a.getBoundingClientRect();return r.width>0&&r.height>0})
    .map(a=>({url:a.href,title:(a.getAttribute('title')||a.textContent||'').trim().slice(0,250)}))
    .filter(x=>/^https:\/\/(www\.)?douyin\.com\/video\/\d{12,25}(?:[/?#]|$)/.test(x.url));
})()"""

_PLAYER_JS = r"""(()=>{
 const vids=Array.from(document.querySelectorAll('video')).filter(v=>{
   const r=v.getBoundingClientRect();return r.width>100&&r.height>100;
 }).sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();
  return y.width*y.height-x.width*x.height;});
 if(!vids.length)return {found:false,url:location.href};
 const v=vids[0],r=v.getBoundingClientRect();
 return {found:true,url:location.href,paused:v.paused,ended:v.ended,
   currentTime:v.currentTime,duration:Number.isFinite(v.duration)?v.duration:null,
   readyState:v.readyState,muted:v.muted,loop:v.loop,
   box:{x:r.x+r.width/2,y:r.y+r.height/2},visibleVideoCount:vids.length};
})()"""

_PREPARE_JS = r"""(()=>{const v=Array.from(document.querySelectorAll('video'))
 .filter(v=>{const r=v.getBoundingClientRect();return r.width>100&&r.height>100;})
 .sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();
 return y.width*y.height-x.width*x.height})[0];
 if(!v)return {ok:false,reason:'no video element'};
 v.pause();v.loop=false;v.muted=false;
 try {v.currentTime=0;} catch(e){return {ok:false,reason:String(e)}}
 return {ok:true,time:v.currentTime};})()"""

_PLAY_JS = r"""(async()=>{const v=Array.from(document.querySelectorAll('video'))
 .filter(v=>{const r=v.getBoundingClientRect();return r.width>100&&r.height>100;})
 .sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();
 return y.width*y.height-x.width*x.height})[0];
 if(!v)return {ok:false,reason:'no video element'};
 try {await v.play();return {ok:!v.paused,paused:v.paused}}
 catch(e){return {ok:false,reason:String(e)}}})()"""


class BrowserError(RuntimeError):
    pass


class BrowserHarness:
    def __init__(self, timeout=60):
        self.timeout=timeout
        self.tab_open=False

    def _run(self, script: str):
        if not shutil.which('browser-harness'):
            raise BrowserError('browser-harness executable not found')
        try:
            out=subprocess.run(['browser-harness'],input=script,capture_output=True,text=True,timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BrowserError('browser-harness timed out') from exc
        if out.returncode:
            raise BrowserError((out.stderr or out.stdout)[-500:])
        for line in reversed(out.stdout.splitlines()):
            if _PREFIX in line:
                try:
                    return json.loads(line.split(_PREFIX,1)[1])
                except ValueError as exc:
                    raise BrowserError('Invalid browser-harness JSON') from exc
        raise BrowserError('No structured result from browser-harness')

    def _eval(self, js_expression: str):
        script=('import json\n'
                'result=js('+json.dumps(js_expression)+')\n'
                'print('+json.dumps(_PREFIX)+'+json.dumps(result,ensure_ascii=False))\n')
        return self._run(script)

    def open(self, url: str):
        # Our caller always validates URL before calling this method.
        cmd='new_tab' if not self.tab_open else 'goto_url'
        script=('import json\n'
                +cmd+'('+json.dumps(url)+')\n'
                +'wait_for_load()\n'
                +'print('+json.dumps(_PREFIX)+'+json.dumps(page_info(),ensure_ascii=False))\n')
        result=self._run(script)
        self.tab_open=True
        return result

    def discover(self, profile: str, scroll_rounds: int = 4) -> list[VideoCandidate]:
        validate_profile_url(profile)
        self.open(profile)
        seen={}
        for i in range(scroll_rounds+1):
            found=self._eval(_DISCOVERY_JS)
            if not isinstance(found,list):
                raise BrowserError('Discovery script returned no list')
            for item in found:
                try:
                    vid=video_id_from_url(item['url'])
                except (KeyError,TypeError,ValueError):
                    continue
                if vid not in seen:
                    seen[vid]=VideoCandidate(vid,item['url'],item.get('title',''))
            if i<scroll_rounds:
                # Use supported CDP scroll helper, not evaluateJs window.scrollBy.
                self._run('import json\nscroll(500,450,dy=650)\nprint('+json.dumps(_PREFIX)+'+json.dumps({"ok":True}))\n')
                time.sleep(1.0)
        return list(seen.values())

    def inspect_player(self):
        return self._eval(_PLAYER_JS)

    def prepare_player(self):
        return self._eval(_PREPARE_JS)

    def play(self):
        result=self._eval(_PLAY_JS)
        if result.get('ok'):
            return result
        # A user gesture may be required. No CAPTCHA/login bypass attempted.
        state=self.inspect_player()
        box=state.get('box')
        if not box:
            return result
        self._run('import json\nclick_at_xy('+str(round(box['x'],2))+','+str(round(box['y'],2))+')\n'
                  'print('+json.dumps(_PREFIX)+'+json.dumps({"clicked":True}))\n')
        return self.inspect_player()

    def wait_for_end(self, expected_url: str, maximum_seconds: float, poll_interval=1.5):
        expected_id=video_id_from_url(expected_url)
        deadline=time.monotonic()+maximum_seconds
        last=None
        last_advance=time.monotonic()
        while time.monotonic()<deadline:
            state=self.inspect_player()
            if not state.get('found'):
                raise BrowserError('Player disappeared')
            try:
                actual_id=video_id_from_url(state.get('url',''))
            except ValueError:
                raise BrowserError('Unexpected navigation while recording')
            if actual_id!=expected_id:
                raise BrowserError('Navigation to another video while recording')
            if state.get('ended'):
                return state
            cur=float(state.get('currentTime') or 0)
            if last is not None and cur<last-1.0:
                raise BrowserError('Video replay/seek during recording; needs review')
            if last is None or cur>last+0.1:
                last_advance=time.monotonic()
            if time.monotonic()-last_advance>18:
                raise BrowserError('Player stalled for >18s')
            last=cur
            time.sleep(poll_interval)
        raise BrowserError('Playback exceeded bounded timeout')

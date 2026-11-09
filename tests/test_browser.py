import json
from types import SimpleNamespace
import pytest
from creator_audio_intel.browser import BrowserHarness, BrowserError, _DISCOVERY_JS


def test_structured_browser_cli(monkeypatch):
    monkeypatch.setattr('shutil.which',lambda name:'/usr/bin/browser-harness')
    calls=[]
    def fake_run(cmd,**kwargs):
        calls.append((cmd,kwargs['input']))
        return SimpleNamespace(returncode=0,stdout='info\nCAI_OUTPUT_JSON:'+json.dumps({'url':'https://www.douyin.com/user/x','title':'home'})+'\n',stderr='')
    monkeypatch.setattr('subprocess.run',fake_run)
    browser=BrowserHarness()
    browser.open('https://www.douyin.com/user/x')
    browser.open('https://www.douyin.com/video/7275916556641652027')
    assert 'new_tab(' in calls[0][1]
    assert 'goto_url(' in calls[1][1]
    assert calls[0][0]==['browser-harness']


def test_no_binary(monkeypatch):
    monkeypatch.setattr('shutil.which',lambda name:None)
    with pytest.raises(BrowserError):BrowserHarness().open('https://www.douyin.com/user/x')


def test_discovery_uses_page_links():
    assert 'querySelectorAll' in _DISCOVERY_JS
    assert 'video' in _DISCOVERY_JS

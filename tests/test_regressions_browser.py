"""Regression tests: one per real defect found while hardening on live hardware.

Each test names the gotcha it pins (see docs/gotchas.md) so that a future change
that reintroduces the bug fails here instead of on someone's machine.
"""
import json
import os
import signal
import subprocess
import sys
import time
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from creator_audio_intel.browser import BrowserError, BrowserHarness, DiscoveryError, _clean_title
from creator_audio_intel.db import DB
from creator_audio_intel.llm import (LLMError, chat_json, normalize_base_url, provider_name,
                                     resolve_config)
from creator_audio_intel.models import Segment, VideoCandidate
from creator_audio_intel.recorder import (FFmpegLoopback, analyze_wav, reap_orphans,
                                          terminate_as_exception)


# --------------------------------------------------------------- browser adapter
def _harness(monkeypatch, responses):
    """BrowserHarness whose subprocess calls return canned stdout lines."""
    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/browser-harness')
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs['input'])
        script = kwargs['input']
        for matcher, payload in responses:
            if matcher is None or matcher in script:
                return SimpleNamespace(returncode=0, stdout='noise\nCAI_OUTPUT_JSON:'
                                       + json.dumps(payload) + '\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='CAI_OUTPUT_JSON:' + json.dumps({'ok': True}) + '\n', stderr='')

    monkeypatch.setattr('subprocess.run', fake_run)
    return BrowserHarness(), calls


def test_gotcha_browser_navigation_race_waits_for_target_document(monkeypatch):
    """GOTCHA-BROWSER-01: goto_url + wait_for_load can report the OLD document."""
    browser, calls = _harness(monkeypatch, [(None, {'url': 'http://x/', 'title': 't'})])
    browser.tab_open = True
    browser.open('http://127.0.0.1:8099/video/9000000000000000000')
    script = calls[0]
    # the navigation is verified, not assumed
    assert 'goto_url(target)' in script
    assert 'document.readyState' in script
    assert 'deadline=time.time()' in script


def test_gotcha_browser_title_marker_stripped():
    """GOTCHA-BROWSER-02: BH_TAB_MARKER rewrites page titles with a horse emoji."""
    assert _clean_title('🐴 HTML Video') == 'HTML Video'
    assert _clean_title('plain') == 'plain'
    assert _clean_title(None) == ''


@pytest.mark.parametrize('diag,code', [
    ({'url': 'chrome-error://chromewebdata/', 'bodySample': 'This site can’t be reached'},
     'ACCESS_DENIED'),
    ({'url': 'https://x/user/a', 'bodySample': '请先登录', 'loginPrompt': True}, 'LOGIN_REQUIRED'),
    ({'url': 'https://x/user/a', 'bodySample': 'x', 'anchorCount': 0, 'videoElements': 0},
     'DOM_CHANGED'),
    ({'url': 'https://x/user/a', 'bodySample': 'nav', 'anchorCount': 4, 'videoElements': 0},
     'EMPTY_PROFILE'),
    ({'url': 'https://x/user/a', 'bodySample': 'nav', 'anchorCount': 4, 'videoElements': 1,
      'videoAnchors': 3}, 'DOM_CHANGED'),
])
def test_gotcha_discovery_taxonomy(diag, code):
    """GOTCHA-BROWSER-03: zero results must be classified, never returned as success."""
    error = BrowserHarness()._classify_empty(diag)
    assert isinstance(error, DiscoveryError)
    assert error.code == code
    assert code in DiscoveryError.CODES


def test_gotcha_discovery_rejects_unknown_code():
    with pytest.raises(ValueError):
        DiscoveryError('MADE_UP')


def test_gotcha_scroll_on_background_tab_activates_and_retries(monkeypatch):
    """GOTCHA-BROWSER-04: scroll() times out on a background tab."""
    attempts = {'n': 0}

    def fake_run(cmd, **kwargs):
        script = kwargs['input']
        if 'scroll(500,450' in script:
            attempts['n'] += 1
            if attempts['n'] == 1:
                return SimpleNamespace(returncode=1, stdout='', stderr='IPCResponseTimeout: timed out after 5s')
            return SimpleNamespace(returncode=0, stdout='CAI_OUTPUT_JSON:{"ok": true}\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='CAI_OUTPUT_JSON:{"activated": true}\n', stderr='')

    monkeypatch.setattr('shutil.which', lambda name: '/usr/bin/browser-harness')
    monkeypatch.setattr('subprocess.run', fake_run)
    browser = BrowserHarness()
    assert browser._scroll_once() is True
    assert attempts['n'] == 2  # once failed, then retried after activation


def test_gotcha_player_must_be_pinned_across_recording():
    """GOTCHA-BROWSER-05: re-picking 'largest visible video' can swap elements."""
    import creator_audio_intel.browser as mod
    assert 'data-cai-player' in mod._PLAYER_JS
    assert 'data-cai-player' in mod._PREPARE_JS
    assert 'scrollIntoView' in mod._PREPARE_JS
    assert 'pinned' in mod._PLAYER_JS


def test_gotcha_metadata_is_polled_not_awaited_in_js():
    """GOTCHA-BROWSER-06: a JS-side await blows the 5s harness IPC budget."""
    import creator_audio_intel.browser as mod
    assert 'await' not in mod._METADATA_JS
    assert "v.preload='auto'" in mod._METADATA_JS

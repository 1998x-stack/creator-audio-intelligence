"""Regression tests: one per real defect found while hardening on live hardware.

Each test names the gotcha it pins (see docs/gotchas.md) so that a future change
that reintroduces the bug fails here instead of on someone's machine.
"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from creator_audio_intel.db import DB
from creator_audio_intel.llm import (LLMError, chat_json, normalize_base_url, provider_name,
                                     resolve_config)
from creator_audio_intel.models import Segment, VideoCandidate


# ------------------------------------------------------------------- LLM client
def test_gotcha_llm_base_url_security():
    """Only HTTPS (or loopback HTTP) endpoints; credentials never in the URL."""
    assert normalize_base_url('https://iagent.iauto.com') == 'https://iagent.iauto.com/v1'
    assert normalize_base_url('https://iagent.iauto.com/v1/') == 'https://iagent.iauto.com/v1'
    assert normalize_base_url('http://127.0.0.1:8080') == 'http://127.0.0.1:8080/v1'
    for bad in ('http://evil.example.com', 'https://u:p@x.com', '', 'ftp://x.com'):
        with pytest.raises(ValueError):
            normalize_base_url(bad)


def test_gotcha_llm_config_precedence(monkeypatch):
    monkeypatch.setenv('IAUTO_BASE_URL', 'https://iagent.iauto.com')
    monkeypatch.setenv('IAUTO_API_KEY', 'env-key')
    monkeypatch.delenv('CAI_LLM_BASE_URL', raising=False)
    monkeypatch.delenv('CAI_LLM_API_KEY', raising=False)
    cfg = resolve_config()
    assert cfg['base_url'] == 'https://iagent.iauto.com/v1'
    assert cfg['api_key'] == 'env-key'
    assert cfg['model'] == 'iagent/standard'
    assert provider_name(cfg['base_url']) == 'iagent.iauto.com'
    # explicit argument wins
    assert resolve_config(model='other/model')['model'] == 'other/model'


def test_gotcha_llm_truncated_completion_is_retried(monkeypatch):
    """GOTCHA-LLM-01: reasoning tokens consumed the budget -> finish_reason=length."""
    calls = {'n': 0}

    def fake_post(url, payload, api_key, timeout):
        calls['n'] += 1
        if calls['n'] == 1:
            return {'choices': [{'message': {'content': '{"summary": "tru'}, 'finish_reason': 'length'}]}
        return {'choices': [{'message': {'content': '{"ok": true}'}, 'finish_reason': 'stop'}]}

    monkeypatch.setattr('creator_audio_intel.llm._post_json', fake_post)
    monkeypatch.setattr('creator_audio_intel.llm.time', SimpleNamespace(sleep=lambda s: None))
    content = chat_json([{'role': 'user', 'content': 'x'}], model='m',
                        base_url='https://iagent.iauto.com/v1', api_key='k', max_attempts=3)
    assert json.loads(content) == {'ok': True}
    assert calls['n'] == 2


def test_gotcha_llm_error_message_hides_api_key(monkeypatch):
    def fake_post(url, payload, api_key, timeout):
        raise OSError('boom')

    monkeypatch.setattr('creator_audio_intel.llm._post_json', fake_post)
    monkeypatch.setattr('creator_audio_intel.llm.time', SimpleNamespace(sleep=lambda s: None))
    with pytest.raises(LLMError) as exc:
        chat_json([{'role': 'user', 'content': 'x'}], model='m',
                  base_url='https://iagent.iauto.com/v1', api_key='super-secret-key', max_attempts=2)
    assert 'super-secret-key' not in str(exc.value)


def test_gotcha_llm_retry_prompt_carries_the_rejection_reason(monkeypatch):
    """GOTCHA-LLM-02: a deterministic rejection needs the reason fed back."""
    import creator_audio_intel.llm as llm
    seen = []

    def fake_chat(messages, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            return json.dumps({'summary': 's', 'claims': [{
                'kind': 'fact', 'statement': 'a', 'segment_ids': [0],
                'evidence_quote': 'invented quote', 'forecast': {}}]})
        return json.dumps({'summary': 's', 'claims': []})

    monkeypatch.setattr(llm, 'chat_json', fake_chat)
    segments = [Segment(0, 0, 1000, '真实原文内容')]
    out = llm._extract_chunk(segments, model='m', base_url='https://x/v1', api_key='k', timeout=1)
    assert out['claims'] == []
    correction = json.dumps(seen[1], ensure_ascii=False)
    assert 'not present in cited transcript' in correction

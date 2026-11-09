"""Evidence-linked structured knowledge extraction through an OpenAI-compatible
chat-completions endpoint.

The project default is the operator's `iagent/standard` endpoint (OpenAI
`/chat/completions` with JSON-schema structured output). Local Ollama is no
longer used.

Two invariants are enforced here, not in the model:
  1. every claim must cite at least one real transcript segment, and
  2. its `evidence_quote` must be a verbatim (whitespace-normalised) substring
     of those segments.
A model that violates either is rejected and retried; a rejected result is
never written to SQLite.

This module intentionally does not score economic forecasts.
"""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .models import Segment

# v2: provider-enforced JSON schema replaces the Ollama `format` field, and the
# prompt now restates the verbatim-quote rule that the validator enforces.
PROMPT_VERSION = 'v2'
KINDS = {'fact', 'causal', 'opinion', 'recommendation', 'forecast'}

DEFAULT_BASE_URL = 'https://iagent.iauto.com/v1'
DEFAULT_MODEL = 'iagent/standard'
# `iagent/standard` (DeepSeek-V4-Flash behind a router) emits hidden reasoning
# tokens that count against max_tokens. 4096 truncated the JSON mid-object
# (finish_reason=length), so the structured-extraction budget is larger.
DEFAULT_MAX_TOKENS = 8192

SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string'},
        'claims': {'type': 'array', 'items': {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string', 'enum': sorted(KINDS)},
                'statement': {'type': 'string'},
                'segment_ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
                'evidence_quote': {'type': 'string'},
                'forecast': {'type': 'object', 'properties': {
                    'metric': {'type': 'string'}, 'direction': {'type': 'string'},
                    'horizon': {'type': 'string'}, 'conditions': {'type': 'string'}},
                    'required': ['metric', 'direction', 'horizon', 'conditions'],
                    'additionalProperties': False}
            },
            'required': ['kind', 'statement', 'segment_ids', 'evidence_quote', 'forecast'],
            'additionalProperties': False}}
    },
    'required': ['summary', 'claims'],
    'additionalProperties': False
}

SYSTEM_PROMPT = """你是严谨的经济学视频资料标注员。输入为视频逐字稿（带段落 ID、起止毫秒）。
请抽取简短的整体摘要和所有能由明确原文支持的核心陈述。
kind 只能是 fact, causal, opinion, recommendation, forecast。
不能把别人观点当成本人观点；不能把条件性预测改成确定预测。
每个 claim 都要填写 segment_ids 和 evidence_quote，quote 必须完整且逐字出现在对应原文片段中，
不得改写、不得翻译、不得补充原文没有的词语；quote 必须可以直接在原文中做字符串匹配。
forecast 只有 kind=forecast 时填写具体信息；kind 不是 forecast 时，forecast 的 metric、direction、horizon、conditions
这四个字段必须全部是空字符串 ""，不允许填任何内容（这是 schema 的硬性要求，填了会被拒绝）。
只输出 JSON，不要使用 Markdown 代码块。"""

# Fed back verbatim when a previous attempt was rejected, so the retry changes
# behaviour instead of repeating an identical temperature-0 sample.
CORRECTION_PROMPT = """上一次输出被拒绝，原因：{reason}
必须修正后重新输出完整 JSON：
1. evidence_quote 必须逐字出现在你引用的 segment 文本中，不能改写、不能拼接、不能跨段。
2. kind 不是 forecast 的 claim，forecast 的四个字段必须全部为空字符串。
3. segment_ids 必须是连续递增的段落编号。"""


class LLMError(RuntimeError):
    """Transport, protocol or schema failure talking to the LLM endpoint."""


def resolve_config(base_url: str | None = None, api_key: str | None = None,
                   model: str | None = None) -> dict:
    """Resolve endpoint settings. Explicit arguments win over environment.

    Precedence: CAI_LLM_* then the pi/iagent IAUTO_* variables.
    Never logs or returns the key in error messages.
    """
    base = (base_url or os.environ.get('CAI_LLM_BASE_URL')
            or os.environ.get('IAUTO_BASE_URL') or DEFAULT_BASE_URL)
    key = (api_key or os.environ.get('CAI_LLM_API_KEY')
           or os.environ.get('IAUTO_API_KEY') or '')
    mdl = (model or os.environ.get('CAI_LLM_MODEL')
           or os.environ.get('IAUTO_MODEL') or DEFAULT_MODEL)
    return {'base_url': normalize_base_url(base), 'api_key': key, 'model': mdl}


def normalize_base_url(base: str) -> str:
    """Validate the endpoint and normalise it to `.../v1`.

    Remote endpoints must be HTTPS; plain HTTP is allowed only on loopback so a
    local proxy stays possible without allowing cleartext keys over a network.
    Credentials embedded in the URL are rejected.
    """
    if not isinstance(base, str) or not base.strip():
        raise ValueError('LLM base URL is empty')
    base = base.strip().rstrip('/')
    parts = urlsplit(base)
    if parts.username or parts.password:
        raise ValueError('LLM base URL must not embed credentials')
    if parts.scheme == 'https':
        pass
    elif parts.scheme == 'http' and parts.hostname in {'localhost', '127.0.0.1', '::1'}:
        pass
    else:
        raise ValueError('LLM base URL must be https:// (or http:// on loopback)')
    if not parts.hostname:
        raise ValueError('LLM base URL has no host')
    if parts.path in ('', '/'):
        return base + '/v1'
    return base


def endpoint(base_url: str) -> str:
    return normalize_base_url(base_url) + '/chat/completions'


def validate_output(data: dict, segments: list[Segment]) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get('summary'), str) or not isinstance(data.get('claims'), list):
        raise ValueError('Malformed LLM extraction result')
    segment_map = {s.segment_id: s for s in segments}
    checked = []
    for item in data['claims']:
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError('Unsupported claim kind')
        ids = item.get('segment_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, int) or isinstance(i, bool) or i not in segment_map for i in ids):
            raise ValueError('Claim has missing transcript segment')
        if ids != list(range(ids[0], ids[-1] + 1)):
            raise ValueError('Evidence segments must be a contiguous increasing range')
        quote = item.get('evidence_quote')
        statement = item.get('statement')
        if not isinstance(quote, str) or not quote.strip() or not isinstance(statement, str) or not statement.strip():
            raise ValueError('Missing statement or direct quote')
        # Keep a single-segment or multi-segment contiguous quote; normalized
        # whitespace only, not a paraphrase.
        haystack = ''.join(segment_map[i].text for i in ids)
        norm = lambda s: ''.join(s.split())
        if norm(quote) not in norm(haystack):
            raise ValueError('Generated quote is not present in cited transcript')
        forecast = item.get('forecast')
        if forecast is None:
            forecast = {}
        if not isinstance(forecast, dict):
            raise ValueError('Forecast must be an object')
        if item['kind'] != 'forecast' and any(forecast.get(k) for k in ('metric', 'direction', 'horizon', 'conditions')):
            raise ValueError('Non-forecast claim contains prediction data')
        checked.append({
            'kind': item['kind'], 'statement': statement,
            'segment_ids': ids, 'evidence_quote': quote,
            'forecast': {k: str(forecast.get(k, '') or '') for k in ('metric', 'direction', 'horizon', 'conditions')}
        })
    return {'summary': data['summary'], 'claims': checked}


def _chunk_segments(segments: list[Segment], max_chars: int = 12000) -> list[list[Segment]]:
    """Split on segment boundaries; never silently truncate a video transcript."""
    if not segments:
        raise ValueError('No transcript segments')
    chunks: list[list[Segment]] = []
    current: list[Segment] = []
    used = 0
    for segment in segments:
        size = len(segment.text) + 120
        if size > max_chars:
            raise ValueError('Single transcript segment exceeds extraction chunk limit')
        if current and used + size > max_chars:
            chunks.append(current)
            current, used = [], 0
        current.append(segment)
        used += size
    if current:
        chunks.append(current)
    return chunks


def _post_json(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _retryable_http(code: int) -> bool:
    return code in (408, 409, 425, 429) or 500 <= code <= 599


def chat_json(messages: list[dict], *, model: str, base_url: str, api_key: str,
              timeout: int = 240, max_tokens: int = 4096,
              response_format: dict | None = None, max_attempts: int = 3) -> str:
    """POST /chat/completions and return the assistant message content.

    Retries transport errors, throttling/5xx, truncated completions and empty
    content with bounded exponential backoff. Raises LLMError after the last
    attempt; the API key is never included in a message.
    """
    if max_attempts < 1:
        raise ValueError('max_attempts must be >= 1')
    url = endpoint(base_url)
    payload = {'model': model, 'temperature': 0, 'max_tokens': max_tokens,
               'messages': messages}
    if response_format is not None:
        payload['response_format'] = response_format
    last = 'unknown error'
    for attempt in range(1, max_attempts + 1):
        try:
            raw = _post_json(url, payload, api_key, timeout)
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                detail = exc.read().decode('utf-8', 'replace')[:200]
            except Exception:
                pass
            last = f'HTTP {exc.code} {detail}'
            if not _retryable_http(exc.code):
                raise LLMError(last) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f'{type(exc).__name__}: {exc}'
        else:
            try:
                choice = raw['choices'][0]
                content = choice['message'].get('content') or ''
                finish = choice.get('finish_reason')
            except (KeyError, IndexError, TypeError) as exc:
                last = 'unexpected response shape from LLM endpoint'
                content, finish = '', None
            if content.strip():
                if finish == 'length':
                    last = 'completion truncated (finish_reason=length)'
                else:
                    return content
            else:
                last = 'LLM endpoint returned empty content'
        if attempt < max_attempts:
            time.sleep(min(2 ** (attempt - 1), 8))
    raise LLMError(f'LLM request failed after {max_attempts} attempts: {last}')


def _extract_chunk(segments: list[Segment], *, model: str, base_url: str, api_key: str,
                   timeout: int, max_attempts: int = 3,
                   max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    payload = [{'id': s.segment_id, 'start_ms': s.start_ms,
                'end_ms': s.end_ms, 'text': s.text} for s in segments]
    user = json.dumps(payload, ensure_ascii=False)
    response_format = {'type': 'json_schema',
                       'json_schema': {'name': 'extraction', 'strict': True, 'schema': SCHEMA}}
    last = 'unknown error'
    for attempt in range(1, max_attempts + 1):
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user}]
        if attempt > 1:
            messages.append({'role': 'system',
                             'content': CORRECTION_PROMPT.format(reason=last)})
        try:
            content = chat_json(messages, model=model, base_url=base_url, api_key=api_key,
                                timeout=timeout, max_tokens=max_tokens,
                                response_format=response_format, max_attempts=2)
        except LLMError as exc:
            # Transport/truncation failures are worth another whole attempt.
            last = str(exc)
            continue
        try:
            generated = json.loads(content)
        except ValueError:
            last = 'LLM returned content that is not valid JSON'
            continue
        try:
            return validate_output(generated, segments)
        except ValueError as exc:
            last = f'LLM output rejected by evidence validator: {exc}'
    raise LLMError(f'Extraction failed validation after {max_attempts} attempts: {last}')


def extract_claims(segments: list[Segment], *, model: str | None = None,
                   base_url: str | None = None, api_key: str | None = None,
                   timeout: int = 300, max_attempts: int = 3,
                   max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """Extract evidence-linked claims for a full transcript.

    Long transcripts are chunked on segment boundaries and merged with
    de-duplication keyed on (kind, statement, segment range) so a retry or a
    chunk overlap cannot create duplicate claims.
    """
    cfg = resolve_config(base_url, api_key, model)
    if not cfg['model']:
        raise ValueError('Provide an LLM model name')
    combined = {'summary': '', 'claims': []}
    seen = set()
    summaries = []
    for chunk in _chunk_segments(segments):
        result = _extract_chunk(chunk, model=cfg['model'], base_url=cfg['base_url'],
                                api_key=cfg['api_key'], timeout=timeout,
                                max_attempts=max_attempts, max_tokens=max_tokens)
        summaries.append(result['summary'])
        for item in result['claims']:
            key = (item['kind'], item['statement'], tuple(item['segment_ids']))
            if key not in seen:
                seen.add(key)
                combined['claims'].append(item)
    combined['summary'] = '\n'.join(s for s in summaries if s)
    return combined


def provider_name(base_url: str | None = None) -> str:
    """Provenance label stored in `llm_runs.provider`."""
    return urlsplit(resolve_config(base_url)['base_url']).hostname or 'unknown'


def healthcheck(base_url: str | None = None, api_key: str | None = None,
                model: str | None = None, timeout: int = 60) -> dict:
    """One tiny structured request; used by doctor/smoke scripts."""
    cfg = resolve_config(base_url, api_key, model)
    if not cfg['api_key']:
        raise LLMError('No LLM API key configured (CAI_LLM_API_KEY or IAUTO_API_KEY)')
    t0 = time.time()
    content = chat_json(
        [{'role': 'user', 'content': 'Reply with JSON {"ok": true} and nothing else.'}],
        model=cfg['model'], base_url=cfg['base_url'], api_key=cfg['api_key'],
        timeout=timeout, max_tokens=100, max_attempts=2,
        response_format={'type': 'json_object'})
    return {'base_url': cfg['base_url'], 'model': cfg['model'],
            'latency_seconds': round(time.time() - t0, 2),
            'reply': content[:200]}

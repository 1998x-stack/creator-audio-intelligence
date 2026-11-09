"""Evidence-linked structured knowledge extraction through local Ollama.

Never allow a generated statement without anchored source segments.
This module intentionally does not automatically grade economic forecasts.
"""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .models import Segment

PROMPT_VERSION='v1'
KINDS={'fact','causal','opinion','recommendation','forecast'}

SCHEMA={
  'type':'object',
  'properties':{
    'summary':{'type':'string'},
    'claims':{'type':'array','items':{
      'type':'object',
      'properties':{
        'kind':{'type':'string','enum':sorted(KINDS)},
        'statement':{'type':'string'},
        'segment_ids':{'type':'array','items':{'type':'integer'},'minItems':1},
        'evidence_quote':{'type':'string'},
        'forecast':{'type':'object','properties':{
          'metric':{'type':'string'},'direction':{'type':'string'},
          'horizon':{'type':'string'},'conditions':{'type':'string'}},
          'required':['metric','direction','horizon','conditions'],
          'additionalProperties':False}
      },
      'required':['kind','statement','segment_ids','evidence_quote','forecast'],
      'additionalProperties':False}}
  },
  'required':['summary','claims'],
  'additionalProperties':False
}

SYSTEM_PROMPT="""你是严谨的经济学视频资料标注员。输入为视频逐字稿（带段落 ID、起止毫秒）。
请抽取简短的整体摘要和所有能由明确原文支持的核心陈述。
kind 只能是 fact, causal, opinion, recommendation, forecast。
不能把别人观点当成本人观点；不能把条件性预测改成确定预测。
每个 claim 都要填写 segment_ids 和 evidence_quote，quote 必须完整且逐字出现在对应原文片段中。
forecast 只有 kind=forecast 时填写具体信息；其他情况使用空字符串，不凭空添加时间范围或指标。
严禁把音频里没有的内容补充进来，不需要外部联网知识。
输出 JSON，不要使用 Markdown。"""


def validate_output(data: dict, segments: list[Segment]) -> dict:
    if not isinstance(data,dict) or not isinstance(data.get('summary'),str) or not isinstance(data.get('claims'),list):
        raise ValueError('Malformed LLM extraction result')
    segment_map={s.segment_id:s for s in segments}
    checked=[]
    for item in data['claims']:
        if not isinstance(item,dict) or item.get('kind') not in KINDS:
            raise ValueError('Unsupported claim kind')
        ids=item.get('segment_ids')
        if not isinstance(ids,list) or not ids or any(not isinstance(i,int) or isinstance(i,bool) or i not in segment_map for i in ids):
            raise ValueError('Claim has missing transcript segment')
        if ids != list(range(ids[0], ids[-1] + 1)):
            raise ValueError('Evidence segments must be a contiguous increasing range')
        quote=item.get('evidence_quote')
        statement=item.get('statement')
        if not isinstance(quote,str) or not quote.strip() or not isinstance(statement,str) or not statement.strip():
            raise ValueError('Missing statement or direct quote')
        # Keep a single-segment or multi-segment contiguous quote; normalized
        # whitespace only, not a paraphrase.
        haystack=''.join(segment_map[i].text for i in ids)
        norm=lambda s:''.join(s.split())
        if norm(quote) not in norm(haystack):
            raise ValueError('Generated quote is not present in cited transcript')
        forecast=item.get('forecast')
        if forecast is None:forecast={}
        if not isinstance(forecast,dict):raise ValueError('Forecast must be an object')
        if item['kind']!='forecast' and any(forecast.get(k) for k in ('metric','direction','horizon','conditions')):
            raise ValueError('Non-forecast claim contains prediction data')
        checked.append({
            'kind':item['kind'],'statement':statement,
            'segment_ids':ids,'evidence_quote':quote,
            'forecast':{k:str(forecast.get(k,'') or '') for k in ('metric','direction','horizon','conditions')}
        })
    return {'summary':data['summary'],'claims':checked}


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


def _ollama_chunk(segments: list[Segment], *, model: str, base_url: str, timeout: int) -> dict:
    payload = [{'id': s.segment_id, 'start_ms': s.start_ms,
                'end_ms': s.end_ms, 'text': s.text} for s in segments]
    body = json.dumps({
        'model': model, 'stream': False, 'format': SCHEMA,
        'options': {'temperature': 0},
        'messages': [{'role': 'system', 'content': SYSTEM_PROMPT},
                     {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}]
    }, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(base_url.rstrip('/') + '/api/chat', data=body,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError('Cannot connect to configured local Ollama service') from exc
    try:
        generated = json.loads(raw['message']['content'])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('Ollama returned invalid JSON') from exc
    return validate_output(generated, segments)


def extract_ollama(segments: list[Segment], *, model: str,
                   base_url='http://127.0.0.1:11434', timeout=240) -> dict:
    if not model:
        raise ValueError('Provide local Ollama model name')
    target = urlsplit(base_url)
    if (target.scheme != 'http' or target.hostname not in {'localhost', '127.0.0.1'}
            or target.username or target.password or target.path not in ('', '/')
            or not target.port):
        raise ValueError('Ollama endpoint must be an explicit localhost port')
    combined = {'summary': '', 'claims': []}
    seen = set()
    summaries = []
    for chunk in _chunk_segments(segments):
        result = _ollama_chunk(chunk, model=model, base_url=base_url, timeout=timeout)
        summaries.append(result['summary'])
        for item in result['claims']:
            key = (item['kind'], item['statement'], tuple(item['segment_ids']))
            if key not in seen:
                seen.add(key)
                combined['claims'].append(item)
    combined['summary'] = '\n'.join(summaries)
    return combined

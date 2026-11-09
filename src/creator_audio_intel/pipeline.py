"""Serial, source-linked browser capture -> local ASR -> local LLM -> SQLite.

The browser adapter requires a real approved Chrome session; offline tests use
fakes and never imply production Douyin compatibility.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import os
import time
from .asr import transcribe_audio
from .browser import BrowserHarness, DiscoveryError
from .db import DB
from .llm import extract_claims, provider_name, resolve_config, PROMPT_VERSION
from .models import Segment
from .recorder import FFmpegLoopback, reap_orphans, terminate_as_exception
from .security import validate_profile_url, video_id_from_url


def process_transcript(db: DB, video_id: int, *, model='small', device='cpu', compute_type='int8') -> int:
    cap = db.latest_capture(video_id)
    if not cap:
        raise ValueError('No QC-passed audio for that video')
    tr = db.latest_transcript(video_id)
    if tr and tr['capture_id'] == cap['id'] and tr['model_name'] == model:
        if db.get_video(video_id)['status'] == 'captured':
            db.transition(video_id, 'transcribed', expected={'captured'})
        return int(tr['id'])
    segments, language, version = transcribe_audio(cap['wav_path'], model_name=model, device=device,
                                                   compute_type=compute_type)
    tid = db.add_transcript(video_id, int(cap['id']), model, language, segments, version)
    db.transition(video_id, 'transcribed', expected={'captured', 'transcribed', 'failed'})
    return tid


def process_llm(db: DB, video_id: int, *, model: str | None = None,
                base_url: str | None = None, api_key: str | None = None) -> int:
    tr = db.latest_transcript(video_id)
    if not tr:
        raise ValueError('No transcript for video')
    cfg = resolve_config(base_url, api_key, model)
    provider = provider_name(cfg['base_url'])
    existing = db.existing_extraction(int(tr['id']), provider, cfg['model'], PROMPT_VERSION)
    if existing:
        if db.get_video(video_id)['status'] == 'transcribed':
            db.transition(video_id, 'analyzed', expected={'transcribed'})
        return int(existing['id'])
    rows = db.segments(int(tr['id']))
    segments = [Segment(r['segment_id'], r['start_ms'], r['end_ms'], r['text']) for r in rows]
    result = extract_claims(segments, model=cfg['model'], base_url=cfg['base_url'],
                            api_key=cfg['api_key'])
    rid = db.add_extraction(video_id, int(tr['id']), provider, cfg['model'], result, PROMPT_VERSION)
    db.transition(video_id, 'analyzed', expected={'transcribed', 'analyzed'})
    return rid


def discover(db: DB, browser: BrowserHarness, profile: str, scroll_rounds: int = 4,
             platform: str = 'douyin') -> tuple[int, int]:
    validate_profile_url(profile, platform)
    creator_id = db.creator(profile, platform)
    candidates = browser.discover(profile, scroll_rounds, platform=platform)
    for video in candidates:
        db.put_video(creator_id, video, platform)
    return creator_id, len(candidates)


def capture_video(db: DB, browser: BrowserHarness, row, *, backend: str, device: str, data_dir: Path):
    """Record one video: open, verify identity, bracket playback with FFmpeg.

    Order is deliberate: the recorder starts before playback so the WAV contains
    the whole performance, and it stops on the player's real `ended` state. A
    capture that fails QC is stored with its reason but never advances the video
    to `captured`.
    """
    vid = int(row['id'])
    page_url = str(row['source_url'])
    platform = str(row['platform'])
    expected_id = video_id_from_url(page_url, platform)
    if expected_id != row['platform_video_id']:
        raise ValueError('Source ID mismatch: refusing to record')
    db.transition(vid, 'recording', expected={'discovered', 'failed'})
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '_' + str(os.getpid())
    wav = data_dir / 'audio' / platform / expected_id / (run_id + '.wav')
    recorder = FFmpegLoopback(wav, backend, device)
    started = False
    try:
        info = browser.open(page_url)
        try:
            actual_id = video_id_from_url(info['url'], platform)
        except (KeyError, ValueError) as exc:
            raise RuntimeError('Page did not resolve to expected video URL') from exc
        if actual_id != expected_id:
            raise RuntimeError('Video identity changed during navigation')
        ready = browser.prepare_player()
        if not ready.get('ok'):
            raise RuntimeError('Cannot prepare video: ' + str(ready))
        # `duration` is null until metadata loads; polling from the host avoids
        # the harness' 5s JS budget and the false 'no suitable player' error.
        metadata = browser.wait_for_metadata(timeout=25)
        duration = metadata.get('duration')
        if (not metadata.get('ok') or isinstance(duration, bool)
                or not isinstance(duration, (float, int)) or not (2 <= duration <= 3600)):
            raise RuntimeError('No suitable finite-duration HTML5 player (2s-60min): ' + str(metadata))
        # Creator ownership still needs independent user-approved homepage proof.
        with terminate_as_exception():
            recorder.start()
            started = True
            played = browser.play()
            if played.get('paused', True):
                raise RuntimeError('Playback did not start (user gesture/DOM may be required): '
                                   + str(played.get('reason', '')))
            browser.wait_for_end(page_url, maximum_seconds=min(float(duration) * 1.8 + 45, 4200),
                                 platform=platform)
            capture = recorder.stop(expected_seconds=float(duration))
            started = False
        cid = db.add_capture(vid, capture)
        if not capture.qc_ok:
            raise RuntimeError('Recording QC failed: ' + capture.qc_reason)
        db.transition(vid, 'captured', expected={'recording'})
        return cid
    except BaseException as exc:
        if started:
            recorder.abort()
        try:
            if db.get_video(vid)['status'] == 'recording':
                db.transition(vid, 'failed', expected={'recording'}, detail=str(exc)[:500])
        except (ValueError, KeyError):
            pass
        raise


def resume_downstream(db: DB, creator_id: int | None = None, *, limit: int = 100,
                      with_asr: bool = True, llm_model: str | None = None,
                      llm_base_url: str | None = None, llm_api_key: str | None = None,
                      asr_device: str = 'cpu', compute_type: str = 'int8') -> dict:
    """Retry downstream stages without re-recording successfully captured audio."""
    if not 1 <= limit <= 10000:
        raise ValueError('limit must be 1-10000')
    if llm_model and not with_asr:
        raise ValueError('LLM requires ASR')
    rows = db.downstream_pending(creator_id=creator_id, limit=limit)
    processed, errors = 0, []
    for row in rows:
        vid = int(row['id'])
        try:
            if with_asr and row['status'] == 'captured':
                process_transcript(db, vid, device=asr_device, compute_type=compute_type)
            if llm_model and db.get_video(vid)['status'] == 'transcribed':
                process_llm(db, vid, model=llm_model, base_url=llm_base_url, api_key=llm_api_key)
            processed += 1
        except (RuntimeError, ValueError, OSError) as exc:
            # Keep stage as captured/transcribed to make retries lossless.
            db.log_event(vid, 'stage_error', str(exc)[:500])
            errors.append({'video_id': row['platform_video_id'], 'reason': str(exc)})
    return {'stage_jobs': len(rows), 'stage_processed': processed, 'stage_errors': errors}


def run_profile(db: DB, profile: str, *, backend: str, device: str, data_dir: Path,
                limit: int = 3, scroll_rounds: int = 4, with_asr: bool = True,
                llm_model: str | None = None, llm_base_url: str | None = None,
                llm_api_key: str | None = None, platform: str = 'douyin',
                asr_device: str = 'cpu', compute_type: str = 'int8') -> dict:
    if not 1 <= limit <= 100:
        raise ValueError('limit must be 1-100')
    browser = BrowserHarness()
    db.reset_interrupted()
    reaped = reap_orphans(data_dir)
    creator_id, discovered = discover(db, browser, profile, scroll_rounds, platform)
    if not discovered and not db.count_videos(creator_id):
        raise RuntimeError('No visible video links found; site adapter may need updating')
    queued = db.pending(creator_id, limit)
    captured, failed = 0, []
    for row in queued:
        try:
            capture_video(db, browser, row, backend=backend, device=device, data_dir=data_dir)
            captured += 1
        except (RuntimeError, ValueError, OSError) as exc:
            failed.append({'video_id': row['platform_video_id'], 'reason': str(exc)})
    stages = resume_downstream(db, creator_id, limit=max(limit, 10), with_asr=with_asr,
                               llm_model=llm_model, llm_base_url=llm_base_url,
                               llm_api_key=llm_api_key,
                               asr_device=asr_device, compute_type=compute_type)
    return {'discovered': discovered, 'queued': len(queued), 'completed': captured,
            'failed': failed, 'orphan_ffmpeg_reaped': reaped, **stages}

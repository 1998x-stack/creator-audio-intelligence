"""Authorized existing WAV import: an offline path to the same SQLite pipeline.

This is useful when automated browser playback is unavailable or site layouts
change. An existing WAV must already be PCM16 mono at 16 kHz.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4
from .db import DB
from .models import VideoCandidate
from .recorder import wav_quality
from .security import validate_profile_url, video_id_from_url


def import_wav(db: DB, *, profile_url: str, video_url: str, wav_path: Path,
               data_dir: Path, rights_confirmed: bool) -> int:
    if not rights_confirmed:
        raise ValueError('Confirm rights before importing audio')
    validate_profile_url(profile_url)
    video_id = video_id_from_url(video_url)
    path = Path(wav_path)
    if not path.is_file():
        raise ValueError('Input WAV does not exist')
    quality = wav_quality(path)
    if not quality.qc_ok:
        raise ValueError('Input WAV failed QC: ' + quality.qc_reason)
    creator_id = db.creator(profile_url)
    vid = db.put_video(creator_id, VideoCandidate(video_id, video_url))
    existing = db.latest_capture(vid)
    if existing and existing['wav_sha256'] == quality.sha256:
        return vid
    if db.get_video(vid)['status'] not in ('discovered', 'failed'):
        raise ValueError('This video already has a processed capture; refusing overwrite')
    safe_filename = (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                     + '_' + uuid4().hex[:8] + '.wav')
    dest = data_dir / 'audio' / video_id / safe_filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    dest_quality = wav_quality(dest)
    if dest_quality.sha256 != quality.sha256:
        dest.unlink(missing_ok=True)
        raise RuntimeError('Copied WAV checksum mismatch')
    db.transition(vid, 'recording', expected={'discovered', 'failed'}, detail='importing approved WAV')
    db.add_capture(vid, dest_quality)
    db.transition(vid, 'captured', expected={'recording'})
    return vid

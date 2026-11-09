"""Regression tests: one per real defect found while hardening on live hardware.

Each test names the gotcha it pins (see docs/gotchas.md) so that a future change
that reintroduces the bug fails here instead of on someone's machine.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from creator_audio_intel.db import DB
from creator_audio_intel.models import Segment, VideoCandidate


# ------------------------------------------------------------------- database
def test_gotcha_sqlite_integrity_fk_and_backup(tmp_path):
    """GOTCHA-SQLITE-01: verify with SQLite's own checks, not by assumption."""
    db = DB(tmp_path / 'a.sqlite3')
    vid = db.put_video(db.creator('https://www.douyin.com/user/x'),
                       VideoCandidate('7275916556641652027', 'https://www.douyin.com/video/7275916556641652027'))
    db.transition(vid, 'recording')
    db.transition(vid, 'failed', detail='boom')
    assert db.integrity_check() == 'ok'
    assert db.foreign_key_check() == []

    backup = tmp_path / 'backup.sqlite3'
    db.backup_to(backup)
    db.close()
    restored = DB(backup)
    assert restored.integrity_check() == 'ok'
    assert restored.get_video(vid)['last_error'] == 'boom'
    restored.close()


def test_gotcha_platform_is_part_of_the_same_video_id_space(tmp_path):
    """A fixture id must not collide with, or be reported as, a douyin video."""
    db = DB(tmp_path / 'p.sqlite3')
    douyin = db.put_video(db.creator('https://www.douyin.com/user/x', 'douyin'),
                          VideoCandidate('9000000000000000000',
                                         'https://www.douyin.com/video/9000000000000000000'), 'douyin')
    fixture = db.put_video(db.creator('http://127.0.0.1:8099/user/x', 'fixture'),
                           VideoCandidate('9000000000000000000',
                                          'http://127.0.0.1:8099/video/9000000000000000000'), 'fixture')
    assert douyin != fixture
    assert db.get_video(douyin)['platform'] == 'douyin'
    assert db.get_video(fixture)['platform'] == 'fixture'
    rows = {(r['platform'], r['status']) for r in db.stats_by_platform()}
    assert ('douyin', 'discovered') in rows and ('fixture', 'discovered') in rows
    db.close()


def test_gotcha_fixture_urls_are_not_accepted_as_douyin():
    from creator_audio_intel.security import validate_profile_url, video_id_from_url
    local = 'http://127.0.0.1:8099/user/creator'
    assert validate_profile_url(local, 'fixture') == local
    with pytest.raises(ValueError):
        validate_profile_url(local, 'douyin')
    assert video_id_from_url('http://127.0.0.1:8099/video/9000000000000000000', 'fixture') \
        == '9000000000000000000'
    with pytest.raises(ValueError):
        video_id_from_url('http://127.0.0.1:8099/video/9000000000000000000', 'douyin')
    with pytest.raises(ValueError):
        validate_profile_url('http://www.douyin.com/user/x', 'douyin')  # http not allowed

"""Regression tests: one per real defect found while hardening on live hardware.

Each test names the gotcha it pins (see docs/gotchas.md) so that a future change
that reintroduces the bug fails here instead of on someone's machine.
"""
import json
import os
import signal
import sys
import time
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from creator_audio_intel.db import DB
from creator_audio_intel.models import Segment, VideoCandidate
from creator_audio_intel.recorder import (FFmpegLoopback, analyze_wav, reap_orphans,
                                          terminate_as_exception)


# ------------------------------------------------------------------- recorder
def _tone(path, seconds=3.0, peak=800):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(array('h', (peak if i % 80 < 40 else -peak
                                      for i in range(int(16000 * seconds)))).tobytes())
    return path


def test_gotcha_audio_qc_reports_peak_silence_and_clipping(tmp_path):
    """GOTCHA-AUDIO-01: RMS alone cannot see clipping or a nearly-silent capture."""
    report = analyze_wav(_tone(tmp_path / 'tone.wav'))
    assert report['format_ok'] and report['peak'] == 800
    assert report['clipped_fraction'] == 0.0 and report['silence_ratio'] < 0.05

    clipped = _tone(tmp_path / 'clip.wav', peak=32767)
    assert analyze_wav(clipped)['clipped_fraction'] > 0.9

    from creator_audio_intel.recorder import wav_quality
    assert wav_quality(clipped).qc_reason == 'clipped'


def test_gotcha_audio_qc_rejects_mostly_silent_with_noise(tmp_path):
    path = tmp_path / 'hiss.wav'
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
        samples = array('h')
        for i in range(16000 * 3):
            samples.append(400 if i < 1600 else 0)  # 3% real signal, rest silence
        handle.writeframes(samples.tobytes())
    from creator_audio_intel.recorder import wav_quality
    assert wav_quality(path).qc_reason == 'mostly_silent'


def test_gotcha_audio_analyze_accepts_str_path(tmp_path):
    assert analyze_wav(str(_tone(tmp_path / 's.wav')))['sample_rate'] == 16000


def test_gotcha_orphan_ffmpeg_reaping_ignores_unrelated_pids(tmp_path, monkeypatch):
    """GOTCHA-AUDIO-02: never kill a recycled pid that is not ffmpeg."""
    killed = []
    monkeypatch.setattr('creator_audio_intel.recorder._pid_alive', lambda pid: True)
    monkeypatch.setattr('creator_audio_intel.recorder.os.kill',
                        lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr('creator_audio_intel.recorder.subprocess.run',
                        lambda *a, **k: SimpleNamespace(stdout='python -m http.server\n'))

    pidfile = tmp_path / 'audio' / 'x.ffmpeg.pid'
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text('4242')
    assert reap_orphans(tmp_path) == []
    assert killed == []
    assert pidfile.exists()  # left alone, not deleted


def test_gotcha_orphan_ffmpeg_reaping_kills_real_ffmpeg(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr('creator_audio_intel.recorder._pid_alive', lambda pid: True)
    monkeypatch.setattr('creator_audio_intel.recorder.os.kill',
                        lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr('creator_audio_intel.recorder.subprocess.run',
                        lambda *a, **k: SimpleNamespace(stdout='ffmpeg -f avfoundation -i :0 out.wav\n'))
    pidfile = tmp_path / 'x.ffmpeg.pid'
    pidfile.write_text('1234')
    assert reap_orphans(tmp_path) == [1234]
    assert killed == [(1234, signal.SIGTERM)]
    assert not pidfile.exists()


def test_gotcha_sigterm_must_unwind_python_so_the_recorder_is_aborted():
    """GOTCHA-AUDIO-03: default SIGTERM kills Python without running cleanup."""
    with pytest.raises(KeyboardInterrupt):
        with terminate_as_exception():
            os.kill(os.getpid(), signal.SIGTERM)


def test_gotcha_sigterm_handler_is_restored():
    before = signal.getsignal(signal.SIGTERM)
    with terminate_as_exception():
        assert signal.getsignal(signal.SIGTERM) is not before or os.name == 'nt'
    assert signal.getsignal(signal.SIGTERM) is before


def test_gotcha_recorder_refuses_bad_device():
    from creator_audio_intel.recorder import audio_input
    for backend, device in (('pulse', ''), ('pulse', 'a\nb'), ('dshow', 'Microphone'),
                            ('nope', 'x')):
        with pytest.raises(ValueError):
            audio_input(backend, device)


def test_gotcha_recorder_start_failure_leaves_no_partial_file(tmp_path):
    recorder = FFmpegLoopback(tmp_path / 'out.wav', 'pulse', '@DEFAULT_MONITOR@')
    recorder.path.parent.mkdir(parents=True, exist_ok=True)
    recorder.tmp.write_bytes(b'partial')
    recorder.abort()
    assert not recorder.tmp.exists()
    assert not recorder.pidfile.exists()

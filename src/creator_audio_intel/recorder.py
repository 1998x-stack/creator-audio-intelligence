"""FFmpeg records a selected OS loopback audio input, not browser action traces.

Capture integrity rules learned on real hardware:
  * FFmpeg must be started before playback and stopped when the page reports
    `ended`, so the WAV brackets the real playback interval.
  * A graceful `q` on stdin finalises the WAV header; SIGKILL does not, which is
    why `.partial.wav` is used and only renamed after a clean exit.
  * A pidfile per recording lets a later run reap an orphaned FFmpeg left by a
    crashed or SIGKILLed parent.
  * SIGTERM does not unwrap Python by default, so a handler is installed while
    recording to guarantee the recorder is aborted.
"""
from __future__ import annotations
from array import array
from contextlib import contextmanager
from pathlib import Path
import hashlib
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import wave
from .models import CaptureResult

KNOWN_BACKENDS = ('pulse', 'avfoundation', 'dshow')

# Silence/clipping gates. These are deliberately loose: they reject a dead or
# distorted recording, they do not judge whether the content is the right one.
SILENCE_FRAME_MS = 10
SILENCE_LEVEL = 20
MAX_SILENCE_RATIO = 0.95
MAX_CLIPPED_FRACTION = 0.02


def audio_input(backend: str, device: str) -> list[str]:
    if backend not in KNOWN_BACKENDS:
        raise ValueError('Expected pulse, avfoundation or dshow')
    if not device or '\n' in device or '\r' in device:
        raise ValueError('Invalid audio device')
    if backend == 'dshow' and not device.startswith('audio='):
        raise ValueError('Windows dshow requires audio=...')
    return ['-f', backend, '-i', device]


def _device_list_command(backend: str) -> list[str] | None:
    if backend == 'avfoundation':
        return ['ffmpeg', '-hide_banner', '-f', 'avfoundation', '-list_devices', 'true', '-i', '']
    return None


def ffmpeg_audio_devices() -> list[str]:
    """Best-effort enumeration of capture-capable audio inputs.

    Returns human-readable labels. Never raises: a missing tool or unparsable
    output yields an empty list so `doctor` can report it as missing rather than
    crashing.
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return []
    if sys.platform == 'darwin':
        try:
            out = subprocess.run(_device_list_command('avfoundation'), capture_output=True,
                                 text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return []
        text = (out.stderr or '') + (out.stdout or '')
        section = text.split('AVFoundation audio devices:')[-1]
        # Names, not indices: AVFoundation indices shift as devices connect and
        # disconnect, so `-i "<index>"` is not reproducible. `ffmpeg -f
        # avfoundation -i ":<name>"` is.
        return [m.group(2).strip() for m in re.finditer(r'\[(\d+)\]\s+([^\n\[]+)', section)]
    if shutil.which('pactl'):
        try:
            out = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True,
                                 text=True, timeout=15)
            return [line.split('\t')[1] for line in out.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired):
            return []
    return []


@contextmanager
def terminate_as_exception():
    """Turn SIGTERM into an exception so recorder cleanup actually runs.

    Without this, `kill <pid>` terminates Python immediately and leaves an
    orphan FFmpeg holding the audio device.
    """
    if os.name == 'nt':
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def handler(signum, frame):
        raise KeyboardInterrupt('SIGTERM received during recording')

    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def analyze_wav(path: Path | str) -> dict:
    """Format, level, silence and clipping report for a PCM16 mono WAV."""
    path = Path(path)
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    with wave.open(str(path), 'rb') as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        count = w.getnframes()
        duration = count / rate if rate else 0.0
        frame_samples = max(1, rate * SILENCE_FRAME_MS // 1000)
        total_squared = 0
        num = 0
        peak = 0
        clipped = 0
        silent_frames = 0
        total_frames = 0
        while True:
            raw = w.readframes(frame_samples)
            if not raw:
                break
            values = array('h')
            values.frombytes(raw)
            if sys.byteorder != 'little':
                values.byteswap()
            if not values:
                continue
            frame_peak = 0
            for v in values:
                iv = int(v)
                total_squared += iv * iv
                if abs(iv) > peak:
                    peak = abs(iv)
                if abs(iv) > frame_peak:
                    frame_peak = abs(iv)
                if abs(iv) >= 32767:
                    clipped += 1
            num += len(values)
            total_frames += 1
            if frame_peak < SILENCE_LEVEL:
                silent_frames += 1
    return {
        'path': str(path),
        'sha256': digest,
        'channels': channels,
        'sample_width_bytes': width,
        'sample_rate': rate,
        'frames': count,
        'duration_seconds': round(duration, 3),
        'rms': round(math.sqrt(total_squared / max(1, num)), 2),
        'peak': peak,
        'peak_dbfs': round(20 * math.log10(peak / 32768), 2) if peak else None,
        'clipped_samples': clipped,
        'clipped_fraction': round(clipped / max(1, num), 6),
        'silence_ratio': round(silent_frames / max(1, total_frames), 4),
        'format_ok': (channels, width, rate) == (1, 2, 16000),
    }


def wav_quality(path: Path, expected_seconds: float | None = None) -> CaptureResult:
    report = analyze_wav(Path(path))
    if not report['format_ok']:
        raise ValueError('WAV should be 16kHz mono PCM16')
    duration = report['duration_seconds']
    rms = report['rms']
    reason = 'ok'
    if duration < 2:
        reason = 'too_short'
    elif rms < 35:
        reason = 'low_audio_energy'
    elif report['silence_ratio'] > MAX_SILENCE_RATIO:
        reason = 'mostly_silent'
    elif report['clipped_fraction'] > MAX_CLIPPED_FRACTION:
        reason = 'clipped'
    elif expected_seconds is not None and abs(duration - expected_seconds) > max(12, expected_seconds * 0.25):
        reason = 'duration_mismatch'
    return CaptureResult(report['path'], report['sha256'], int(duration * 1000), rms,
                         reason == 'ok', reason,
                         peak=int(report['peak']),
                         silence_ratio=float(report['silence_ratio']),
                         clipped_fraction=float(report['clipped_fraction']))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_orphans(data_dir: Path, log=print) -> list[int]:
    """Kill FFmpeg processes left behind by a crashed recorder.

    Only processes whose pidfile sits under `data_dir`, whose pid is alive, and
    whose command line mentions ffmpeg are signalled: a recycled pid belonging
    to an unrelated program is never touched.
    """
    killed: list[int] = []
    root = Path(data_dir)
    if not root.exists():
        return killed
    for pidfile in root.rglob('*.ffmpeg.pid'):
        try:
            pid = int(pidfile.read_text(encoding='utf-8').strip())
        except (ValueError, OSError):
            pidfile.unlink(missing_ok=True)
            continue
        if not _pid_alive(pid):
            pidfile.unlink(missing_ok=True)
            continue
        cmdline = ''
        try:
            raw = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
            cmdline = raw
        except OSError:
            try:
                out = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)],
                                     capture_output=True, text=True, timeout=10)
                cmdline = out.stdout
            except (OSError, subprocess.TimeoutExpired):
                cmdline = ''
        if 'ffmpeg' not in cmdline:
            log(f'  pid {pid} from {pidfile.name} is alive but is not ffmpeg; leaving it alone')
            continue
        log(f'  killing orphan ffmpeg pid {pid} (from {pidfile})')
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            pass
        pidfile.unlink(missing_ok=True)
    return killed


class FFmpegLoopback:
    def __init__(self, filename: Path, backend: str, device: str):
        self.path = Path(filename)
        self.tmp = self.path.with_suffix('.partial.wav')
        self.pidfile = self.path.with_suffix('.ffmpeg.pid')
        self.backend = backend
        self.device = device
        self.proc = None
        self.log = None
        self.started_at = None

    def _write_pidfile(self):
        if self.proc is not None:
            self.pidfile.parent.mkdir(parents=True, exist_ok=True)
            self.pidfile.write_text(str(self.proc.pid), encoding='utf-8')

    def _clear_pidfile(self):
        self.pidfile.unlink(missing_ok=True)

    def start(self):
        if self.proc is not None:
            raise RuntimeError('Already recording')
        if not shutil.which('ffmpeg'):
            raise RuntimeError('ffmpeg not installed')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp.unlink(missing_ok=True)
        self.log = open(self.path.with_suffix('.ffmpeg.log'), 'wb')
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-y',
               *audio_input(self.backend, self.device),
               '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(self.tmp)]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL, stderr=self.log)
            self._write_pidfile()
            self.started_at = time.monotonic()
            time.sleep(0.5)
            if self.proc.poll() is not None:
                raise RuntimeError('FFmpeg exited immediately; inspect ' + str(self.path.with_suffix('.ffmpeg.log')))
        except BaseException:
            self.abort()
            raise

    def stop(self, expected_seconds: float | None = None) -> CaptureResult:
        if not self.proc:
            raise RuntimeError('Recorder not started')
        p = self.proc
        ret = None
        try:
            if p.stdin:
                try:
                    p.stdin.write(b'q\n')
                    p.stdin.flush()
                    p.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                ret = p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    ret = p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
                raise RuntimeError('FFmpeg did not exit cleanly')
        finally:
            self.proc = None
            self._clear_pidfile()
            if self.log:
                self.log.close()
                self.log = None
        if ret != 0 or not self.tmp.exists():
            raise RuntimeError('Recording failed; check ' + str(self.path.with_suffix('.ffmpeg.log')))
        self.tmp.replace(self.path)
        return wav_quality(self.path, expected_seconds)

    def abort(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self.proc = None
        self._clear_pidfile()
        if self.log:
            self.log.close()
            self.log = None
        self.tmp.unlink(missing_ok=True)

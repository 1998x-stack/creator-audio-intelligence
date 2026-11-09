"""FFmpeg records a selected OS loopback audio input, not browser action traces."""
from __future__ import annotations
from array import array
from pathlib import Path
import hashlib
import math
import shutil
import subprocess
import sys
import time
import wave
from .models import CaptureResult


def audio_input(backend: str, device: str) -> list[str]:
    if backend not in ('pulse','avfoundation','dshow'):
        raise ValueError('Expected pulse, avfoundation or dshow')
    if not device or '\n' in device or '\r' in device:
        raise ValueError('Invalid audio device')
    if backend=='dshow' and not device.startswith('audio='):
        raise ValueError('Windows dshow requires audio=...')
    return ['-f',backend,'-i',device]


def wav_quality(path: Path, expected_seconds: float | None = None) -> CaptureResult:
    with path.open('rb') as source:
        digest=hashlib.file_digest(source,'sha256').hexdigest()
    with wave.open(str(path),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,16000):
            raise ValueError('WAV should be 16kHz mono PCM16')
        count=w.getnframes()
        duration=count/16000
        # Evaluate all samples for small clips; streaming chunks avoid huge RAM.
        total_squared=0
        num=0
        while True:
            raw=w.readframes(16000)
            if not raw:break
            values=array('h')
            values.frombytes(raw)
            if sys.byteorder!='little':values.byteswap()
            # Loopback silence test; cannot prove speech correctness.
            total_squared+=sum(int(v)*int(v) for v in values)
            num+=len(values)
    rms=math.sqrt(total_squared/max(1,num))
    reason='ok'
    if duration<2:reason='too_short'
    elif rms<35:reason='low_audio_energy'
    elif expected_seconds is not None and abs(duration-expected_seconds)>max(12,expected_seconds*0.25):
        reason='duration_mismatch'
    return CaptureResult(str(path),digest,int(duration*1000),round(rms,2),reason=='ok',reason)


class FFmpegLoopback:
    def __init__(self, filename: Path, backend: str, device: str):
        self.path=Path(filename)
        self.tmp=self.path.with_suffix('.partial.wav')
        self.backend=backend
        self.device=device
        self.proc=None
        self.log=None

    def start(self):
        if self.proc is not None:raise RuntimeError('Already recording')
        if not shutil.which('ffmpeg'):raise RuntimeError('ffmpeg not installed')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.log=open(self.path.with_suffix('.ffmpeg.log'),'wb')
        cmd=['ffmpeg','-hide_banner','-loglevel','warning','-y',*audio_input(self.backend,self.device),
             '-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(self.tmp)]
        try:
            self.proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=self.log)
            time.sleep(0.5)
            if self.proc.poll() is not None:
                raise RuntimeError('FFmpeg exited; inspect .ffmpeg.log')
        except BaseException:
            self.abort()
            raise

    def stop(self, expected_seconds:float | None = None) -> CaptureResult:
        if not self.proc:raise RuntimeError('Recorder not started')
        p=self.proc
        try:
            if p.stdin:
                p.stdin.write(b'q\n');p.stdin.flush();p.stdin.close()
            try:ret=p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
                raise RuntimeError('FFmpeg did not exit cleanly')
        finally:
            self.proc=None
            if self.log:self.log.close();self.log=None
        if ret!=0 or not self.tmp.exists():
            raise RuntimeError('Recording failed; check .ffmpeg.log')
        self.tmp.replace(self.path)
        return wav_quality(self.path,expected_seconds)

    def abort(self):
        if self.proc:
            self.proc.terminate()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)
            self.proc=None
        if self.log:self.log.close();self.log=None
        self.tmp.unlink(missing_ok=True)

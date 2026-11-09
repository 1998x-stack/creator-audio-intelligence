"""Local multilingual Whisper small, via faster-whisper/CTranslate2.

Real-capture lesson: a loopback source is often far quieter than the audio the
model was tuned on (an acoustic speaker->microphone route peaked near -20 dBFS,
which Whisper decoded as hallucinated repetition). Quiet captures are therefore
peak-normalised *in memory* before decoding. The stored WAV is never modified,
and the applied preprocessing version is recorded in `transcripts.model_version`
so a transcript can always be traced back to the exact decoder input rule.
"""
from __future__ import annotations
from array import array
from pathlib import Path
import math
import sys
import wave
from .models import Segment

# Bump when the in-memory preprocessing rule changes.
PREPROCESS_VERSION = 'peaknorm-v1'
TARGET_PEAK_DBFS = -3.0
MAX_GAIN_DB = 20.0


def _read_mono_pcm16(path: str | Path) -> tuple[array, int]:
    with wave.open(str(path), 'rb') as handle:
        if (handle.getnchannels(), handle.getsampwidth()) != (1, 2):
            raise ValueError('ASR input must be mono PCM16 (run wav_quality first)')
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    samples = array('h')
    samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples, rate


def peak_normalize(path: str | Path, *, target_dbfs: float = TARGET_PEAK_DBFS,
                   max_gain_db: float = MAX_GAIN_DB):
    """Return (float32 numpy samples in [-1, 1], applied gain in dB).

    Gain is bounded and only ever raises the level; already-loud audio is passed
    through untouched so this cannot introduce clipping on good captures.
    faster-whisper requires an ndarray, not a list, to skip its own decode step.
    """
    import numpy as np
    samples, rate = _read_mono_pcm16(path)
    audio = np.frombuffer(samples.tobytes(), dtype='<i2').astype('float32') / 32768.0
    if audio.size == 0:
        return audio.astype('float32'), 0.0
    peak = float(np.max(np.abs(audio)))
    if peak <= 0:
        return audio.astype('float32'), 0.0
    gain_db = min(max_gain_db, max(0.0, target_dbfs - 20 * math.log10(peak)))
    if gain_db <= 0:
        return audio.astype('float32'), 0.0
    return np.clip(audio * (10 ** (gain_db / 20.0)), -1.0, 1.0).astype('float32'), round(gain_db, 2)


def transcribe_audio(path: str | Path, *, device='cpu', compute_type='int8',
                     model_name='small') -> tuple[list[Segment], str, str]:
    try:
        from faster_whisper import WhisperModel
        import faster_whisper
    except ImportError as exc:
        raise RuntimeError('Install package with: pip install -e ".[asr]"') from exc
    audio, gain_db = peak_normalize(path)
    # Multilingual "small", not English-only "small.en". Model downloads at first use.
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, meta = model.transcribe(audio, language='zh', beam_size=5, vad_filter=True,
                                      word_timestamps=False, condition_on_previous_text=False)
    valid = [s for s in segments if s.text.strip()]
    records = [Segment(i, int(s.start * 1000), int(s.end * 1000), s.text.strip())
               for i, s in enumerate(valid)]
    if not records:
        raise RuntimeError('Whisper returned no speech segments')
    version = f"{getattr(faster_whisper, '__version__', 'unknown')}+{PREPROCESS_VERSION}"
    if gain_db:
        version += f'+gain{gain_db}dB'
    return records, meta.language, version

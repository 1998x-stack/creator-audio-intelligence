import math
import wave
import pytest
from creator_audio_intel.recorder import audio_input,wav_quality


def write_wav(path, n_seconds, amplitude):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
        from array import array
        data=array('h',(int(amplitude*math.sin(2*math.pi*i/80)) for i in range(int(16000*n_seconds))))
        w.writeframes(data.tobytes())


def test_audio_quality(tmp_path):
    path=tmp_path/'beep.wav';write_wav(path,3,800)
    r=wav_quality(path,3)
    assert r.qc_ok and 2900<r.duration_ms<3100 and len(r.sha256)==64
    assert r.rms>35


def test_silence_fails(tmp_path):
    path=tmp_path/'silence.wav';write_wav(path,3,0)
    assert wav_quality(path,3).qc_reason=='low_audio_energy'


def test_audio_source_validation():
    assert audio_input('pulse','@DEFAULT_MONITOR@')==['-f','pulse','-i','@DEFAULT_MONITOR@']
    with pytest.raises(ValueError):audio_input('dshow','Microphone')

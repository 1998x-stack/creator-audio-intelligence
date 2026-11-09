import wave
import pytest
from creator_audio_intel.db import DB
from creator_audio_intel.models import VideoCandidate, Segment
from creator_audio_intel.ingest import import_wav
from creator_audio_intel.llm import _chunk_segments, validate_output
from creator_audio_intel.pipeline import resume_downstream

PROFILE = 'https://www.douyin.com/user/test-creator'
VIDEO = 'https://www.douyin.com/video/7275916556641652027'


def _wav(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((b'\0\x10') * (16000 * 3))


def test_import_wav_idempotent_and_search(tmp_path):
    db = DB(tmp_path / 'db.sqlite')
    original = tmp_path / 'input.wav'
    _wav(original)
    v1 = import_wav(db, profile_url=PROFILE, video_url=VIDEO, wav_path=original,
                    data_dir=tmp_path / 'data', rights_confirmed=True)
    v2 = import_wav(db, profile_url=PROFILE, video_url=VIDEO, wav_path=original,
                    data_dir=tmp_path / 'data', rights_confirmed=True)
    assert v1 == v2
    assert db.get_video(v1)['status'] == 'captured'
    assert db.conn.execute('SELECT COUNT(*) FROM captures').fetchone()[0] == 1
    cap = db.latest_capture(v1)
    db.add_transcript(v1, cap['id'], 'small', 'zh', [Segment(0,0,1000,'通缩风险')])
    assert db.search('通缩')[0]['platform_video_id'] == '7275916556641652027'
    assert db.search('%') == []
    db.close()


def test_import_requires_permission(tmp_path):
    db = DB(tmp_path / 'db.sqlite')
    path = tmp_path / 'sound.wav'
    _wav(path)
    with pytest.raises(ValueError, match='rights'):
        import_wav(db, profile_url=PROFILE, video_url=VIDEO, wav_path=path,
                   data_dir=tmp_path, rights_confirmed=False)
    db.close()


def test_chunks_and_quote_hardening():
    rows = [Segment(i, i*1000, (i+1)*1000, '经济' * 2400) for i in range(7)]
    chunks = _chunk_segments(rows, max_chars=10000)
    assert sum(len(c) for c in chunks) == len(rows)
    assert len(chunks) >= 4
    with pytest.raises(ValueError, match='contiguous'):
        validate_output({'summary':'x','claims':[{
            'kind':'opinion', 'statement':'a', 'segment_ids':[0,2],
            'evidence_quote':'经济', 'forecast':{}}]}, rows)


def test_resume_stages_without_second_capture(monkeypatch, tmp_path):
    import creator_audio_intel.pipeline as p
    db = DB(tmp_path / 'db.sqlite')
    vid = db.put_video(db.creator(PROFILE), VideoCandidate('7275916556641652027', VIDEO))
    wav = tmp_path / 'raw.wav'
    _wav(wav)
    from creator_audio_intel.recorder import wav_quality
    db.transition(vid, 'recording', expected={'discovered'})
    db.add_capture(vid, wav_quality(wav))
    db.transition(vid, 'captured', expected={'recording'})
    calls = []
    def fake_asr(path, **kwargs):
        calls.append('asr')
        return [Segment(0,0,2000,'宏观经济')], 'zh', 'fake'
    monkeypatch.setattr(p, 'transcribe_audio', fake_asr)
    first = resume_downstream(db, limit=1)
    assert first['stage_processed'] == 1
    assert db.get_video(vid)['status'] == 'transcribed'
    second = resume_downstream(db, limit=1)
    assert second['stage_processed'] == 1
    assert calls == ['asr']
    db.close()


def test_single_writer_lock(tmp_path):
    from creator_audio_intel.lock import RunLock
    path = tmp_path / 's.db'
    with RunLock(path):
        with pytest.raises(RuntimeError, match='Another CAI worker'):
            with RunLock(path):
                pass
    with RunLock(path):
        pass


def test_asr_nonempty_segments_have_contiguous_ids(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    import creator_audio_intel.asr as asr
    fake = ModuleType('faster_whisper')
    fake.__version__ = 'fake1'
    class FakeWhisper:
        def __init__(self, *args, **kwargs): pass
        def transcribe(self, path, **kwargs):
            segments = [SimpleNamespace(start=0,end=1,text='经济'),
                        SimpleNamespace(start=1,end=2,text='   '),
                        SimpleNamespace(start=2,end=3,text='增长')]
            return iter(segments), SimpleNamespace(language='zh')
    fake.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake)
    segments, lang, version = asr.transcribe_audio('fake.wav')
    assert [s.segment_id for s in segments] == [0, 1]
    assert [s.text for s in segments] == ['经济','增长']
    assert lang == 'zh'
    assert version == 'fake1'

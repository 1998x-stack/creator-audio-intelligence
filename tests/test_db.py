import sqlite3
import pytest
from creator_audio_intel.db import DB
from creator_audio_intel.models import VideoCandidate, Segment, CaptureResult

PROFILE='https://www.douyin.com/user/creator_sample'
URL='https://www.douyin.com/video/7275916556641652027'


def test_sqlite_crud_and_dedup(tmp_path):
    db=DB(tmp_path/'test.sqlite3')
    cid=db.creator(PROFILE)
    assert db.creator(PROFILE)==cid
    vid=db.put_video(cid,VideoCandidate('7275916556641652027',URL,'第一个标题'))
    assert db.put_video(cid,VideoCandidate('7275916556641652027',URL,'新标题'))==vid
    assert db.get_video(vid)['title']=='新标题'
    assert len(db.pending(cid,3))==1
    db.transition(vid,'recording',expected={'discovered'})
    assert db.get_video(vid)['attempts']==1
    with pytest.raises(ValueError):db.transition(vid,'recording',expected={'discovered'})
    db.transition(vid,'failed',expected={'recording'},detail='device failure')
    assert len(db.pending(cid,3))==1
    assert db.reset_interrupted()==0
    assert db.conn.execute('PRAGMA foreign_keys').fetchone()[0]==1
    assert db.conn.execute('PRAGMA journal_mode').fetchone()[0].lower()=='wal'
    db.close()


def test_crash_recovery_and_attempt_limit(tmp_path):
    db=DB(tmp_path/'d.db')
    cid=db.creator(PROFILE)
    vid=db.put_video(cid,VideoCandidate('7275916556641652027',URL))
    db.transition(vid,'recording')
    assert db.reset_interrupted()==1
    assert db.get_video(vid)['status']=='failed'
    for _ in range(2):
        db.transition(vid,'recording',expected={'failed'})
        db.transition(vid,'failed',expected={'recording'})
    assert db.get_video(vid)['attempts']==3
    assert len(db.pending(cid,20))==0
    db.close()


def test_transcript_and_llm_provenance(tmp_path):
    db=DB(tmp_path/'d.db')
    cid=db.creator(PROFILE)
    vid=db.put_video(cid,VideoCandidate('7275916556641652027',URL))
    cap=CaptureResult('audio/test.wav','c'*64,4000,600.0,True,'ok')
    capid=db.add_capture(vid,cap)
    assert db.add_capture(vid,cap)==capid
    db.transition(vid,'captured')
    tid=db.add_transcript(vid,capid,'small','zh',[
        Segment(0,0,2000,'今年消费增速可能下降。'),
        Segment(1,2000,4000,'但是也存在不确定性。')],version='1.2')
    assert db.add_transcript(vid,capid,'small','zh',[Segment(0,0,2000,'今年消费增速可能下降。')])==tid
    db.transition(vid,'transcribed')
    rid=db.add_extraction(vid,tid,'ollama','local-model',{
        'summary':'谈论消费走势',
        'claims':[{'kind':'forecast','statement':'消费可能下降','segment_ids':[0],
                   'evidence_quote':'消费增速可能下降','forecast':{'metric':'消费增速','direction':'decrease','horizon':'','conditions':''}}]
    })
    assert rid>0
    assert db.conn.execute('SELECT count(*) FROM claims').fetchone()[0]==1
    assert db.conn.execute('SELECT count(*) FROM transcript_segments').fetchone()[0]==2
    db.close()

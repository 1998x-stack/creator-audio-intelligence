from creator_audio_intel.db import DB
from creator_audio_intel.models import CaptureResult, Segment,VideoCandidate
from creator_audio_intel.pipeline import run_profile,process_transcript,process_llm

PROFILE='https://www.douyin.com/user/creator_sample'
URL='https://www.douyin.com/video/7275916556641652027'


def test_offline_transcript_to_extraction(monkeypatch,tmp_path):
    db=DB(tmp_path/'state.db')
    cid=db.creator(PROFILE)
    vid=db.put_video(cid,VideoCandidate('7275916556641652027',URL))
    db.add_capture(vid,CaptureResult('/tmp/mock.wav','d'*64,4000,400.0,True,'ok'))
    db.transition(vid,'captured')
    monkeypatch.setattr('creator_audio_intel.pipeline.transcribe_audio',
        lambda *a,**k:([Segment(0,0,2000,'消费可能走弱。')],'zh','test-v1'))
    tid=process_transcript(db,vid)
    assert tid>0
    monkeypatch.setattr('creator_audio_intel.pipeline.extract_claims',
        lambda segments,**kwargs:{'summary':'消费展望','claims':[
            {'kind':'forecast','statement':'消费可能走弱','segment_ids':[0],
             'evidence_quote':'消费可能走弱','forecast':{'metric':'消费','direction':'decrease','horizon':'','conditions':''}}]})
    rid=process_llm(db,vid,model='fake-local')
    assert rid>0
    assert db.get_video(vid)['status']=='analyzed'
    db.close()

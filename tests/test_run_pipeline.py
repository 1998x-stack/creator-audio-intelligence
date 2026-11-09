from types import SimpleNamespace
from creator_audio_intel.db import DB
from creator_audio_intel.models import VideoCandidate,CaptureResult
from creator_audio_intel.pipeline import run_profile

PROFILE='https://www.douyin.com/user/test_owner'
VIDEO1='7275916556641652027'
VIDEO2='7371745609449098522'

class FakeBrowser:
    def __init__(self):self.active=None
    def discover(self,profile,scroll_rounds):
        return [VideoCandidate(VIDEO1,'https://www.douyin.com/video/'+VIDEO1),
                VideoCandidate(VIDEO2,'https://www.douyin.com/video/'+VIDEO2)]
    def open(self,url):
        self.active=url
        return {'url':url,'title':'test'}
    def inspect_player(self):return {'found':True,'duration':3.0,'url':self.active,'paused':True}
    def prepare_player(self):return {'ok':True,'time':0}
    def play(self):return {'paused':False,'ok':True}
    def wait_for_end(self,url,maximum_seconds):return {'ended':True}

class FakeRecorder:
    created=[]
    def __init__(self,filename,backend,device):
        self.filename=filename
        self.started=False
        self.__class__.created.append(self)
    def start(self):self.started=True
    def stop(self,expected_seconds):
        assert self.started
        self.started=False
        return CaptureResult(str(self.filename),'a'*63+str(len(self.__class__.created)),3000,400.0,True,'ok')
    def abort(self):self.started=False


def test_serial_two_videos_no_duplicate(monkeypatch,tmp_path):
    import creator_audio_intel.pipeline as p
    FakeRecorder.created=[]
    browser=FakeBrowser()
    monkeypatch.setattr(p,'BrowserHarness',lambda:browser)
    monkeypatch.setattr(p,'FFmpegLoopback',FakeRecorder)
    db=DB(tmp_path/'db.sqlite3')
    first=run_profile(db,PROFILE,backend='pulse',device='monitor',data_dir=tmp_path,limit=2,with_asr=False)
    assert first['completed']==2
    assert len(FakeRecorder.created)==2
    second=run_profile(db,PROFILE,backend='pulse',device='monitor',data_dir=tmp_path,limit=2,with_asr=False)
    assert second['queued']==0
    assert db.stats()['captured']==2
    db.close()


def test_no_visible_links_report_error(monkeypatch,tmp_path):
    import pytest
    import creator_audio_intel.pipeline as p
    class EmptyBrowser(FakeBrowser):
        def discover(self,profile,scroll_rounds):return []
    monkeypatch.setattr(p,'BrowserHarness',lambda:EmptyBrowser())
    db=DB(tmp_path/'d.db')
    with pytest.raises(RuntimeError,match='No visible video'):
        run_profile(db,PROFILE,backend='pulse',device='monitor',data_dir=tmp_path)
    db.close()

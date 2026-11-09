"""CLI with explicit consent and incremental stage commands."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import sys
from .db import DB
from .pipeline import discover,run_profile,process_transcript,process_llm,resume_downstream
from .browser import BrowserHarness
from .security import validate_profile_url
from .ingest import import_wav
from .lock import RunLock


def parser():
    p=argparse.ArgumentParser(prog='cai',description='Creator Audio Intelligence (experimental Douyin capture)')
    p.add_argument('--db',default='data/creator_intel.sqlite3',help='SQLite database location')
    s=p.add_subparsers(dest='cmd',required=True)
    s.add_parser('init')
    s.add_parser('doctor')
    d=s.add_parser('discover',help='Discover visible video links; no recording')
    d.add_argument('--profile',required=True)
    d.add_argument('--scroll-rounds',type=int,default=4)
    r=s.add_parser('run',help='Experimental serial capture, ASR, LLM')
    r.add_argument('--profile',required=True)
    r.add_argument('--backend',choices=['pulse','avfoundation','dshow'],required=True)
    r.add_argument('--device',required=True,help='Configured loopback INPUT (not speaker output)')
    r.add_argument('--limit',type=int,default=3)
    r.add_argument('--scroll-rounds',type=int,default=4)
    r.add_argument('--data-dir',type=Path,default=Path('data'))
    r.add_argument('--asr-model',choices=['small'],default='small')
    r.add_argument('--asr-device',choices=['cpu','cuda'],default='cpu')
    r.add_argument('--compute-type',default='int8')
    r.add_argument('--skip-asr',action='store_true')
    r.add_argument('--llm-provider',choices=['ollama'],default='ollama')
    r.add_argument('--llm-model',help='Installed Ollama model, e.g. qwen3:8b; optional')
    r.add_argument('--rights-ok',action='store_true',help='Confirm permission to record and process these videos')
    t=s.add_parser('transcribe',help='Transcribe an already captured WAV and store in SQLite')
    t.add_argument('--video-row-id',type=int,required=True)
    t.add_argument('--device',choices=['cpu','cuda'],default='cpu')
    t.add_argument('--compute-type',default='int8')
    x=s.add_parser('extract',help='Use local Ollama on an existing transcript')
    x.add_argument('--video-row-id',type=int,required=True)
    x.add_argument('--llm-model',required=True)
    imp=s.add_parser('import-wav',help='Import an existing authorized 16kHz mono PCM WAV')
    imp.add_argument('--profile',required=True)
    imp.add_argument('--video-url',required=True)
    imp.add_argument('--wav',type=Path,required=True)
    imp.add_argument('--data-dir',type=Path,default=Path('data'))
    imp.add_argument('--rights-ok',action='store_true')
    resume=s.add_parser('resume',help='Retry unfinished ASR/LLM stages without re-recording')
    resume.add_argument('--limit',type=int,default=100)
    resume.add_argument('--asr-device',choices=['cpu','cuda'],default='cpu')
    resume.add_argument('--compute-type',default='int8')
    resume.add_argument('--llm-model',help='Optional installed Ollama model')
    search=s.add_parser('search',help='Search saved transcripts and titles')
    search.add_argument('query')
    search.add_argument('--limit',type=int,default=20)
    s.add_parser('stats')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.cmd=='doctor':
        for app in ('browser-harness','ffmpeg','ollama'):
            print(f'{app}: {shutil.which(app) or "not found"}')
        print('Chrome CDP and loopback device routing require hands-on verification.')
        return 0
    db=DB(args.db)
    try:
        if args.cmd=='init':print('Initialized',db.path)
        elif args.cmd=='discover':
            if not 0<=args.scroll_rounds<=30:raise ValueError('Invalid scroll rounds')
            cid,n=discover(db,BrowserHarness(),args.profile,args.scroll_rounds)
            print(json.dumps({'creator_row_id':cid,'visible_videos_found':n},ensure_ascii=False))
        elif args.cmd=='run':
            validate_profile_url(args.profile)
            if not args.rights_ok:raise ValueError('Recording rights not confirmed; pass --rights-ok only with permission')
            if args.skip_asr and args.llm_model:raise ValueError('LLM requires ASR; cannot combine with --skip-asr')
            if not 0<=args.scroll_rounds<=30:raise ValueError('Invalid scroll rounds')
            with RunLock(args.db):
                result=run_profile(db,args.profile,backend=args.backend,device=args.device,data_dir=args.data_dir,
                                   limit=args.limit,scroll_rounds=args.scroll_rounds,
                                   with_asr=not args.skip_asr,llm_model=args.llm_model,
                                   asr_device=args.asr_device,compute_type=args.compute_type)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.cmd=='transcribe':print('transcript id',process_transcript(db,args.video_row_id,device=args.device,compute_type=args.compute_type))
        elif args.cmd=='extract':print('LLM run id',process_llm(db,args.video_row_id,model=args.llm_model))
        elif args.cmd=='import-wav':
            print('video row id', import_wav(db,profile_url=args.profile,video_url=args.video_url,
                                            wav_path=args.wav,data_dir=args.data_dir,
                                            rights_confirmed=args.rights_ok))
        elif args.cmd=='resume':
            with RunLock(args.db):
                result=resume_downstream(db,limit=args.limit,asr_device=args.asr_device,
                                         compute_type=args.compute_type,llm_model=args.llm_model)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.cmd=='search':
            print(json.dumps(db.search(args.query, args.limit),ensure_ascii=False,indent=2))
        elif args.cmd=='stats':print(json.dumps(db.stats(),ensure_ascii=False,indent=2))
        return 0
    except (RuntimeError,ValueError,OSError) as exc:
        print('ERROR:',exc,file=sys.stderr)
        return 1
    finally:db.close()


if __name__=='__main__':raise SystemExit(main())

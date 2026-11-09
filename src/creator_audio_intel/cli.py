"""CLI with explicit consent and incremental stage commands."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from .db import DB
from .pipeline import discover, run_profile, process_transcript, process_llm, resume_downstream
from .browser import BrowserHarness, BrowserError, DiscoveryError
from .security import validate_profile_url
from .ingest import import_wav
from .lock import RunLock
from .llm import resolve_config, healthcheck, LLMError
from .recorder import KNOWN_BACKENDS


def parser():
    p = argparse.ArgumentParser(prog='cai', description='Creator Audio Intelligence (local-first capture pipeline)')
    p.add_argument('--db', default='data/creator_intel.sqlite3', help='SQLite database location')
    s = p.add_subparsers(dest='cmd', required=True)
    s.add_parser('init')
    doc = s.add_parser('doctor', help='Report environment readiness (no side effects)')
    doc.add_argument('--llm-check', action='store_true',
                     help='Also make one real call to the configured LLM endpoint')
    d = s.add_parser('discover', help='Discover visible video links; no recording')
    d.add_argument('--profile', required=True)
    d.add_argument('--platform', choices=['douyin', 'fixture'], default='douyin')
    d.add_argument('--scroll-rounds', type=int, default=4)
    r = s.add_parser('run', help='Serial capture, ASR, LLM')
    r.add_argument('--profile', required=True)
    r.add_argument('--platform', choices=['douyin', 'fixture'], default='douyin')
    r.add_argument('--backend', choices=sorted(KNOWN_BACKENDS), required=True)
    r.add_argument('--device', required=True, help='Configured loopback INPUT (not speaker output)')
    r.add_argument('--limit', type=int, default=3)
    r.add_argument('--scroll-rounds', type=int, default=4)
    r.add_argument('--data-dir', type=Path, default=Path('data'))
    r.add_argument('--asr-model', default='small', help='faster-whisper model name')
    r.add_argument('--asr-device', choices=['cpu', 'cuda'], default='cpu')
    r.add_argument('--compute-type', default='int8')
    r.add_argument('--skip-asr', action='store_true')
    r.add_argument('--llm-model', help='Model id, e.g. iagent/standard (default: env or built-in)')
    r.add_argument('--llm-base-url', help='OpenAI-compatible base URL (default: CAI_LLM_BASE_URL/IAUTO_BASE_URL)')
    r.add_argument('--rights-ok', action='store_true',
                   help='Confirm permission to record and process these videos')
    t = s.add_parser('transcribe', help='Transcribe an already captured WAV and store in SQLite')
    t.add_argument('--video-row-id', type=int, required=True)
    t.add_argument('--asr-model', default='small')
    t.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    t.add_argument('--compute-type', default='int8')
    x = s.add_parser('extract', help='Extract evidence-linked claims from an existing transcript')
    x.add_argument('--video-row-id', type=int, required=True)
    x.add_argument('--llm-model')
    x.add_argument('--llm-base-url')
    imp = s.add_parser('import-wav', help='Import an existing authorized 16kHz mono PCM WAV')
    imp.add_argument('--profile', required=True)
    imp.add_argument('--platform', choices=['douyin', 'fixture'], default='douyin')
    imp.add_argument('--video-url', required=True)
    imp.add_argument('--wav', type=Path, required=True)
    imp.add_argument('--data-dir', type=Path, default=Path('data'))
    imp.add_argument('--rights-ok', action='store_true')
    resume = s.add_parser('resume', help='Retry unfinished ASR/LLM stages without re-recording')
    resume.add_argument('--limit', type=int, default=100)
    resume.add_argument('--asr-device', choices=['cpu', 'cuda'], default='cpu')
    resume.add_argument('--compute-type', default='int8')
    resume.add_argument('--llm-model')
    resume.add_argument('--llm-base-url')
    search = s.add_parser('search', help='Search saved transcripts and titles')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=20)
    ev = s.add_parser('evidence', help='Print the full provenance chain for one video row')
    ev.add_argument('--video-row-id', type=int, required=True)
    s.add_parser('stats')
    return p


def _doctor(db: DB, llm_check: bool) -> int:
    from .recorder import ffmpeg_audio_devices
    problems = []
    for app in ('browser-harness', 'ffmpeg', 'ffprobe'):
        path = shutil.which(app)
        print(f'{app}: {path or "NOT FOUND"}')
        if not path:
            problems.append(f'{app} missing')
    cdp = os.environ.get('BU_CDP_URL') or '(unset: browser-harness discovers local Chrome)'
    print(f'BU_CDP_URL: {cdp}')
    print(f'database: {db.path}')
    ok = db.conn.execute('PRAGMA integrity_check').fetchone()[0]
    print(f'sqlite integrity_check: {ok}')
    if ok != 'ok':
        problems.append('sqlite integrity_check failed')
    print(f'sqlite foreign_keys: {db.conn.execute("PRAGMA foreign_keys").fetchone()[0]}'
          f' journal_mode: {db.conn.execute("PRAGMA journal_mode").fetchone()[0]}')
    print('audio input devices (loopback source required for capture):')
    devices = ffmpeg_audio_devices()
    if devices:
        for dev in devices:
            print(f'  - {dev}')
    else:
        print('  (none detected; run scripts/inspect_audio.sh)')
        problems.append('no audio input device detected')
    cfg = resolve_config()
    key_state = 'set' if cfg['api_key'] else 'MISSING'
    print(f'llm endpoint: {cfg["base_url"]} model={cfg["model"]} api_key={key_state}')
    if not cfg['api_key']:
        problems.append('no LLM API key (set CAI_LLM_API_KEY or IAUTO_API_KEY)')
    if llm_check:
        try:
            print('llm check:', json.dumps(healthcheck(), ensure_ascii=False))
        except LLMError as exc:
            print(f'llm check FAILED: {exc}')
            problems.append('llm healthcheck failed')
    print()
    if problems:
        print('BLOCKING ISSUES:')
        for item in problems:
            print(f'  * {item}')
        return 1
    print('doctor: no blocking issues detected')
    return 0


def _evidence(db: DB, video_id: int) -> int:
    video = db.get_video(video_id)
    if video is None:
        print(f'ERROR: no video row {video_id}', file=sys.stderr)
        return 1
    out = {'video': dict(video)}
    cap = db.conn.execute('SELECT * FROM captures WHERE video_id=? ORDER BY id DESC LIMIT 1', (video_id,)).fetchone()
    out['capture'] = dict(cap) if cap else None
    tr = db.latest_transcript(video_id)
    out['transcript'] = ({k: tr[k] for k in ('id', 'model_name', 'model_version', 'language', 'created_at')}
                         if tr else None)
    if tr:
        out['segments'] = [dict(r) for r in db.segments(int(tr['id']))]
    runs = db.conn.execute('SELECT * FROM llm_runs WHERE transcript_id=? ORDER BY id', (int(tr['id']),)).fetchall() if tr else []
    out['llm_runs'] = [{'id': r['id'], 'provider': r['provider'], 'model': r['model'],
                        'prompt_version': r['prompt_version'], 'summary': r['summary']} for r in runs]
    out['claims'] = [dict(r) for r in db.conn.execute(
        'SELECT id,kind,statement,evidence_quote,evidence_segment_ids_json,metric,direction,horizon,conditions '
        'FROM claims WHERE video_id=? ORDER BY id', (video_id,)).fetchall()]
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.cmd == 'doctor':
        db = DB(args.db)
        try:
            return _doctor(db, args.llm_check)
        finally:
            db.close()
    db = DB(args.db)
    try:
        if args.cmd == 'init':
            print('Initialized', db.path)
        elif args.cmd == 'discover':
            if not 0 <= args.scroll_rounds <= 30:
                raise ValueError('Invalid scroll rounds')
            cid, n = discover(db, BrowserHarness(), args.profile, args.scroll_rounds,
                              getattr(args, 'platform', 'douyin'))
            print(json.dumps({'creator_row_id': cid, 'visible_videos_found': n}, ensure_ascii=False))
        elif args.cmd == 'run':
            validate_profile_url(args.profile, args.platform)
            if not args.rights_ok:
                raise ValueError('Recording rights not confirmed; pass --rights-ok only with permission')
            if args.skip_asr and args.llm_model:
                raise ValueError('LLM requires ASR; cannot combine with --skip-asr')
            if not 0 <= args.scroll_rounds <= 30:
                raise ValueError('Invalid scroll rounds')
            with RunLock(args.db):
                result = run_profile(db, args.profile, backend=args.backend, device=args.device,
                                     data_dir=args.data_dir, limit=args.limit,
                                     scroll_rounds=args.scroll_rounds,
                                     with_asr=not args.skip_asr, llm_model=args.llm_model,
                                     llm_base_url=args.llm_base_url,
                                     platform=args.platform,
                                     asr_device=args.asr_device, compute_type=args.compute_type)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.cmd == 'transcribe':
            print('transcript id', process_transcript(db, args.video_row_id,
                                                      model=args.asr_model,
                                                      device=args.device,
                                                      compute_type=args.compute_type))
        elif args.cmd == 'extract':
            print('LLM run id', process_llm(db, args.video_row_id, model=args.llm_model,
                                             base_url=args.llm_base_url))
        elif args.cmd == 'import-wav':
            print('video row id', import_wav(db, profile_url=args.profile, video_url=args.video_url,
                                             wav_path=args.wav, data_dir=args.data_dir,
                                             rights_confirmed=args.rights_ok,
                                             platform=args.platform))
        elif args.cmd == 'resume':
            with RunLock(args.db):
                result = resume_downstream(db, limit=args.limit, asr_device=args.asr_device,
                                           compute_type=args.compute_type, llm_model=args.llm_model,
                                           llm_base_url=args.llm_base_url)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.cmd == 'search':
            print(json.dumps(db.search(args.query, args.limit), ensure_ascii=False, indent=2))
        elif args.cmd == 'evidence':
            return _evidence(db, args.video_row_id)
        elif args.cmd == 'stats':
            print(json.dumps(db.stats(), ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, LLMError, sqlite3.Error) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == '__main__':
    raise SystemExit(main())

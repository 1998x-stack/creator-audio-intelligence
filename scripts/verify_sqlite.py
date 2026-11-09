#!/usr/bin/env python3
"""Verify the SQLite system of record: integrity, constraints, referential chain,
state machine, duplicate suppression and backup/restore.

Exit codes: 0 all checks passed, 1 a check failed, 3 database missing, 2 bad usage.

Usage:
    scripts/verify_sqlite.py [DB] [--backup-to PATH] [--quiet]
"""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path

EXPECTED_TABLES = {'creators', 'videos', 'captures', 'transcripts', 'transcript_segments',
                   'llm_runs', 'claims', 'job_events'}
VALID_STATES = {'discovered', 'recording', 'captured', 'transcribed', 'analyzed', 'failed', 'blocked'}


class Verifier:
    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.problems: list[str] = []
        self.notes: list[str] = []

    def say(self, msg=''):
        if not self.quiet:
            print(msg)

    def check(self, name: str, ok: bool, detail: str = ''):
        self.say(f'  [{"ok  " if ok else "FAIL"}] {name}' + (f' {detail}' if detail and not ok else ''))
        if not ok:
            self.problems.append(f'{name} {detail}'.strip())
        return ok

    def note(self, msg: str):
        self.notes.append(msg)
        self.say(f'  [note ] {msg}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('db', nargs='?', default='data/creator_intel.sqlite3')
    ap.add_argument('--backup-to')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    path = Path(args.db)
    if not path.is_file():
        print(f'FAIL: database not found: {path}', file=sys.stderr)
        return 3

    verify = Verifier(args.quiet)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    verify.say(f'database: {path}  ({path.stat().st_size} bytes)')

    verify.say('== pragmas')
    verdict = conn.execute('PRAGMA integrity_check').fetchone()[0]
    verify.check('integrity_check', verdict == 'ok', str(verdict))
    mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
    verify.check('journal_mode is wal', mode.lower() == 'wal', f'got {mode}')
    fk = conn.execute('PRAGMA foreign_key_check').fetchall()
    verify.check('foreign_key_check clean', not fk, str([dict(r) for r in fk[:3]]))

    verify.say('== tables')
    found = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = EXPECTED_TABLES - found
    verify.check('all expected tables exist', not missing, f'missing {sorted(missing)}')

    verify.say('== referential chain')
    for label, sql in (
        ('every video has a creator',
         'SELECT COUNT(*) FROM videos v LEFT JOIN creators c ON c.id=v.creator_id WHERE c.id IS NULL'),
        ('every capture has a video',
         'SELECT COUNT(*) FROM captures c LEFT JOIN videos v ON v.id=c.video_id WHERE v.id IS NULL'),
        ('every segment belongs to a transcript',
         'SELECT COUNT(*) FROM transcript_segments s LEFT JOIN transcripts t ON t.id=s.transcript_id WHERE t.id IS NULL'),
        ('every claim belongs to an llm_run',
         'SELECT COUNT(*) FROM claims c LEFT JOIN llm_runs l ON l.id=c.llm_run_id WHERE l.id IS NULL'),
    ):
        orphans = conn.execute(sql).fetchone()[0]
        verify.check(label, orphans == 0, f'{orphans} orphans')

    # A QC-failed capture is kept for diagnosis, but it must never have advanced
    # the video past 'recording'.
    premature = conn.execute('''SELECT COUNT(*) FROM videos v WHERE v.status IN
        ('captured','transcribed','analyzed') AND NOT EXISTS
        (SELECT 1 FROM captures c WHERE c.video_id=v.id AND c.qc_ok=1)''').fetchone()[0]
    verify.check('no video advanced without a QC-passed capture', premature == 0, f'{premature} videos')

    caps = [r['wav_path'] for r in conn.execute('SELECT wav_path FROM captures')]
    if caps:
        absent = [p for p in caps if not Path(p).exists()]
        verify.check('capture wav files exist on disk', not absent,
                     f'{len(absent)} missing, e.g. {absent[:2]}')
    else:
        verify.note('no captures recorded yet, so WAV existence was not checked')

    bad_refs, bad_quotes = 0, 0
    for row in conn.execute('''SELECT c.evidence_quote, c.evidence_segment_ids_json, lr.transcript_id
                               FROM claims c JOIN llm_runs lr ON lr.id=c.llm_run_id'''):
        ids = json.loads(row['evidence_segment_ids_json'])
        texts = {r[0]: r[1] for r in conn.execute(
            'SELECT segment_id, text FROM transcript_segments WHERE transcript_id=?',
            (row['transcript_id'],))}
        if not set(ids) <= set(texts):
            bad_refs += 1
        hay = ''.join(texts.get(i, '') for i in ids)
        if ''.join(row['evidence_quote'].split()) not in ''.join(hay.split()):
            bad_quotes += 1
    verify.check('claim segment ids resolve inside their transcript', bad_refs == 0, f'{bad_refs} bad')
    verify.check('every claim quote is verbatim in its cited segments', bad_quotes == 0,
                 f'{bad_quotes} unverified')

    verify.say('== state machine')
    unknown = [r[0] for r in conn.execute('SELECT DISTINCT status FROM videos') if r[0] not in VALID_STATES]
    verify.check('all video statuses are known states', not unknown, f'unknown {unknown}')
    stuck = conn.execute("SELECT COUNT(*) FROM videos WHERE status='recording'").fetchone()[0]
    if stuck:
        verify.note(f'{stuck} video(s) left in recording; the next run reclassifies them as failed')

    verify.say('== duplicate suppression')
    dupes = conn.execute('''SELECT platform, platform_video_id, COUNT(*) n FROM videos
                            GROUP BY platform, platform_video_id HAVING n>1''').fetchall()
    verify.check('no duplicate (platform, video_id)', not dupes, str([dict(r) for r in dupes[:3]]))
    runs = conn.execute('''SELECT transcript_id, provider, model, prompt_version, COUNT(*) n
                           FROM llm_runs GROUP BY 1,2,3,4 HAVING n>1''').fetchall()
    verify.check('no duplicate llm_run for transcript/provider/model/prompt', not runs,
                 str([dict(r) for r in runs[:3]]))

    verify.say('== backup / restore')
    if args.backup_to:
        target = sqlite3.connect(args.backup_to)
        try:
            with target:
                conn.backup(target)
        finally:
            target.close()
        verify.check('backup file written', Path(args.backup_to).is_file())
        restored = sqlite3.connect(args.backup_to)
        restored_verdict = restored.execute('PRAGMA integrity_check').fetchone()[0]
        verify.check('backup passes integrity_check', restored_verdict == 'ok', restored_verdict)
        same = {t: restored.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in sorted(EXPECTED_TABLES)}
        live = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in sorted(EXPECTED_TABLES)}
        verify.check('backup row counts match the live database', same == live, f'{same} != {live}')
        restored.close()
    else:
        verify.note('backup not exercised (pass --backup-to PATH to include it)')

    tables = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in sorted(EXPECTED_TABLES)}
    conn.close()

    verify.say('== row counts')
    verify.say('   ' + ', '.join(f'{k}={v}' for k, v in tables.items()))

    if verify.notes:
        print('\nNOTES (not failures):')
        for item in verify.notes:
            print(f'  * {item}')
    print()
    if verify.problems:
        print(f'FAIL: {len(verify.problems)} problem(s)')
        for item in verify.problems:
            print(f'  * {item}')
        return 1
    print('PASS: database verification clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

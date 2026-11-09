"""SQLite is the system of record. Media bytes remain on local disk."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from .models import VideoCandidate, Segment, CaptureResult


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'douyin',
    profile_url TEXT NOT NULL UNIQUE,
    display_name TEXT,
    platform_user_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    creator_id INTEGER NOT NULL REFERENCES creators(id),
    platform TEXT NOT NULL,
    platform_video_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'discovered'
      CHECK(status IN ('discovered','recording','captured','transcribed','analyzed','failed','blocked')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE(platform,platform_video_id)
);
CREATE INDEX IF NOT EXISTS idx_video_creator_status ON videos(creator_id,status);
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    wav_path TEXT NOT NULL,
    wav_sha256 TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    rms REAL NOT NULL,
    qc_ok INTEGER NOT NULL CHECK(qc_ok IN (0,1)),
    qc_reason TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE(video_id,wav_sha256)
);
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    capture_id INTEGER NOT NULL REFERENCES captures(id),
    model_name TEXT NOT NULL,
    model_version TEXT,
    language TEXT NOT NULL,
    transcript_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(capture_id,model_name)
);
CREATE TABLE IF NOT EXISTS transcript_segments (
    id INTEGER PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    segment_id INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(transcript_id,segment_id)
);
CREATE INDEX IF NOT EXISTS idx_segment_transcript_time ON transcript_segments(transcript_id,start_ms);
CREATE TABLE IF NOT EXISTS llm_runs (
    id INTEGER PRIMARY KEY,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    summary TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    llm_run_id INTEGER NOT NULL REFERENCES llm_runs(id),
    video_id INTEGER NOT NULL REFERENCES videos(id),
    kind TEXT NOT NULL CHECK(kind IN ('fact','causal','opinion','recommendation','forecast')),
    statement TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    evidence_segment_ids_json TEXT NOT NULL,
    metric TEXT,
    direction TEXT,
    horizon TEXT,
    conditions TEXT,
    verification_status TEXT NOT NULL DEFAULT 'not_evaluated'
);
CREATE INDEX IF NOT EXISTS idx_claim_kind ON claims(kind);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    old_state TEXT,
    new_state TEXT NOT NULL,
    detail TEXT,
    happened_at TEXT NOT NULL
);
"""


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def creator(self, profile: str, platform: str = 'douyin') -> int:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO creators(platform,profile_url,created_at) VALUES (?,?,?)",
                              (platform, profile, now()))
        return int(self.conn.execute("SELECT id FROM creators WHERE profile_url=?", (profile,)).fetchone()[0])

    def put_video(self, creator_id: int, candidate: VideoCandidate, platform: str = 'douyin') -> int:
        with self.conn:
            self.conn.execute("""INSERT INTO videos(creator_id,platform,platform_video_id,source_url,title,published_at,discovered_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform,platform_video_id) DO UPDATE SET
                title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE videos.title END,
                published_at=COALESCE(videos.published_at,excluded.published_at)
                """, (creator_id, platform, candidate.video_id, candidate.url, candidate.title,
                      candidate.published_at, now()))
        return int(self.conn.execute("SELECT id FROM videos WHERE platform=? AND platform_video_id=?",
                                     (platform, candidate.video_id)).fetchone()[0])

    def get_video(self, video_id: int):
        return self.conn.execute("SELECT * FROM videos WHERE id=?",(video_id,)).fetchone()

    def pending(self, creator_id: int, limit: int):
        return self.conn.execute("SELECT * FROM videos WHERE creator_id=? AND status IN ('discovered','failed') AND attempts<3 ORDER BY id LIMIT ?", (creator_id,limit)).fetchall()

    def transition(self, video_id: int, state: str, *, expected: set[str] | None = None, detail: str = ""):
        valid = {'discovered','recording','captured','transcribed','analyzed','failed','blocked'}
        if state not in valid:
            raise ValueError(f"Unknown state: {state}")
        with self.conn:
            old = self.get_video(video_id)
            if old is None:
                raise ValueError("Video missing")
            if expected is not None and old['status'] not in expected:
                raise ValueError(f"Illegal transition {old['status']} -> {state}")
            if state == 'recording':
                self.conn.execute("UPDATE videos SET status=?,attempts=attempts+1,last_error=NULL WHERE id=?",(state,video_id))
            else:
                self.conn.execute("UPDATE videos SET status=?,last_error=? WHERE id=?",(state,detail or None,video_id))
            self.conn.execute("INSERT INTO job_events(video_id,old_state,new_state,detail,happened_at) VALUES (?,?,?,?,?)",
                              (video_id,old['status'],state,detail,now()))

    def reset_interrupted(self) -> int:
        """Recording rows left after abnormal process exit are retryable failures."""
        with self.conn:
            rows=self.conn.execute("SELECT id FROM videos WHERE status='recording'").fetchall()
            for row in rows:
                self.transition(int(row[0]),'failed',detail='interrupted recording; inspect possible orphan FFmpeg process')
        return len(rows)

    def add_capture(self, video_id: int, capture: CaptureResult) -> int:
        with self.conn:
            self.conn.execute("""INSERT OR IGNORE INTO captures(video_id,wav_path,wav_sha256,duration_ms,rms,qc_ok,qc_reason,captured_at)
                   VALUES (?,?,?,?,?,?,?,?)""",(video_id,capture.wav_path,capture.sha256,capture.duration_ms,capture.rms,int(capture.qc_ok),capture.qc_reason,now()))
        return int(self.conn.execute("SELECT id FROM captures WHERE video_id=? AND wav_sha256=?", (video_id,capture.sha256)).fetchone()[0])

    def count_videos(self, creator_id: int) -> int:
        return int(self.conn.execute('SELECT COUNT(*) FROM videos WHERE creator_id=?',
                                     (creator_id,)).fetchone()[0])

    def downstream_pending(self, *, creator_id: int | None = None, limit: int = 100):
        if creator_id is None:
            return self.conn.execute("SELECT * FROM videos WHERE status IN ('captured','transcribed') ORDER BY id LIMIT ?", (limit,)).fetchall()
        return self.conn.execute("SELECT * FROM videos WHERE creator_id=? AND status IN ('captured','transcribed') ORDER BY id LIMIT ?", (creator_id, limit)).fetchall()

    def log_event(self, video_id: int, event: str, detail: str = '') -> None:
        with self.conn:
            self.conn.execute('INSERT INTO job_events(video_id,old_state,new_state,detail,happened_at) VALUES (?,?,?,?,?)',
                              (video_id, self.get_video(video_id)['status'], event, detail, now()))

    def existing_extraction(self, transcript_id: int, provider: str, model: str, prompt_version: str):
        return self.conn.execute('SELECT * FROM llm_runs WHERE transcript_id=? AND provider=? AND model=? AND prompt_version=? ORDER BY id DESC LIMIT 1',
                                 (transcript_id, provider, model, prompt_version)).fetchone()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        if not query or len(query) > 300 or not 1 <= limit <= 1000:
            raise ValueError('Query length or limit invalid')
        literal = "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        rows = self.conn.execute('''SELECT v.platform_video_id, v.source_url, v.title, t.id AS transcript_id,
                                  substr(t.transcript_text,1,600) AS excerpt
                                  FROM transcripts t JOIN videos v ON v.id=t.video_id
                                  WHERE t.transcript_text LIKE ? ESCAPE '!' OR v.title LIKE ? ESCAPE '!'
                                  ORDER BY t.id DESC LIMIT ?''', (literal, literal, limit)).fetchall()
        return [dict(r) for r in rows]

    def latest_capture(self, video_id: int):
        return self.conn.execute("SELECT * FROM captures WHERE video_id=? AND qc_ok=1 ORDER BY id DESC LIMIT 1",(video_id,)).fetchone()

    def add_transcript(self, video_id: int, capture_id: int, model: str, language: str, segments: list[Segment], version: str | None = None):
        full='\n'.join(s.text for s in segments)
        if not full.strip():
            raise ValueError('Empty transcript')
        with self.conn:
            self.conn.execute("""INSERT OR IGNORE INTO transcripts(video_id,capture_id,model_name,model_version,language,transcript_text,created_at)
                               VALUES (?,?,?,?,?,?,?)""",(video_id,capture_id,model,version,language,full,now()))
            tid=int(self.conn.execute("SELECT id FROM transcripts WHERE capture_id=? AND model_name=?",(capture_id,model)).fetchone()[0])
            for s in segments:
                self.conn.execute("INSERT OR IGNORE INTO transcript_segments(transcript_id,segment_id,start_ms,end_ms,text) VALUES(?,?,?,?,?)",
                                  (tid,s.segment_id,s.start_ms,s.end_ms,s.text))
        return tid

    def latest_transcript(self, video_id: int):
        return self.conn.execute("SELECT * FROM transcripts WHERE video_id=? ORDER BY id DESC LIMIT 1",(video_id,)).fetchone()

    def segments(self, transcript_id: int):
        return self.conn.execute("SELECT segment_id,start_ms,end_ms,text FROM transcript_segments WHERE transcript_id=? ORDER BY segment_id",(transcript_id,)).fetchall()

    def add_extraction(self, video_id: int, transcript_id: int, provider: str, model: str, output: dict, prompt_version='v1'):
        with self.conn:
            cursor=self.conn.execute("INSERT INTO llm_runs(transcript_id,provider,model,prompt_version,summary,raw_json,created_at) VALUES (?,?,?,?,?,?,?)",
                                    (transcript_id,provider,model,prompt_version,output['summary'],json.dumps(output,ensure_ascii=False),now()))
            rid=cursor.lastrowid
            for c in output['claims']:
                f=c.get('forecast') or {}
                self.conn.execute("""INSERT INTO claims(llm_run_id,video_id,kind,statement,evidence_quote,evidence_segment_ids_json,metric,direction,horizon,conditions)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(rid,video_id,c['kind'],c['statement'],c['evidence_quote'],json.dumps(c['segment_ids']),
                  f.get('metric'),f.get('direction'),f.get('horizon'),f.get('conditions')))
        return rid

    def stats(self):
        return {r['status']:r['n'] for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM videos GROUP BY status")}

    def stats_by_platform(self):
        return [dict(r) for r in self.conn.execute(
            'SELECT platform, status, COUNT(*) AS n FROM videos GROUP BY platform, status ORDER BY platform, status')]

    def integrity_check(self) -> str:
        return str(self.conn.execute('PRAGMA integrity_check').fetchone()[0])

    def foreign_key_check(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute('PRAGMA foreign_key_check')]

    def backup_to(self, path: str | Path) -> None:
        """Consistent online backup via the SQLite backup API."""
        target = sqlite3.connect(str(path))
        try:
            with target:
                self.conn.backup(target)
        finally:
            target.close()

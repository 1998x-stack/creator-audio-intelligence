"""Single-host exclusive run lock to avoid concurrent Chrome/loopback writers."""
from pathlib import Path
import os


class RunLock:
    def __init__(self, db_path: str | Path):
        self.path = Path(str(db_path) + '.run.lock')
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if self.file.read(1) == b'':
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('Another CAI worker already holds the single-recording lock') from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.file is None:
            return
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()
        self.file = None

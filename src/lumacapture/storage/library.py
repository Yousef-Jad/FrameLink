from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import time
import uuid


class Library:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "library.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, path TEXT UNIQUE NOT NULL,
                    created REAL NOT NULL, metadata TEXT NOT NULL, thumbnail TEXT,
                    original TEXT, status TEXT NOT NULL DEFAULT 'Original');
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, settings TEXT NOT NULL,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            conn.execute(
                "UPDATE jobs SET status='interrupted', error='Application stopped during compression' WHERE status='running'"
            )

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def add(self, path, metadata, session=None, thumbnail=None, original=None):
        ident = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?)",
                (
                    ident,
                    session or ident,
                    str(Path(path).resolve()),
                    time.time(),
                    json.dumps(metadata),
                    str(thumbnail) if thumbnail else None,
                    original,
                    "Compressed" if original else "Original",
                ),
            )
        return ident

    def recordings(self):
        with self.connect() as conn:
            return [
                dict(row) | {"metadata": json.loads(row["metadata"])}
                for row in conn.execute("SELECT * FROM recordings ORDER BY created DESC")
            ]

    def get(self, ident):
        return next((r for r in self.recordings() if r["id"] == ident), None)

    def rename(self, ident, name):
        from lumacapture.media.model import safe_filename, atomic_promote

        item = self.get(ident)
        if item is None:
            raise ValueError("Recording no longer exists")
        old = Path(item["path"])
        new = old.with_name(safe_filename(name) + old.suffix)
        if old == new:
            return
        atomic_promote(old, new)
        try:
            with self.connect() as conn:
                conn.execute("UPDATE recordings SET path=? WHERE id=?", (str(new), ident))
        except Exception:
            atomic_promote(new, old)
            raise

    def delete(self, ident):
        item = self.get(ident)
        if item:
            Path(item["path"]).unlink(missing_ok=True)
            with self.connect() as conn:
                conn.execute("DELETE FROM recordings WHERE id=?", (ident,))

    def setting(self, key, default=None):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def enqueue(self, recording_id, settings):
        ident = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id,recording_id,settings,status,created) VALUES (?,?,?,?,?)",
                (ident, recording_id, json.dumps(settings), "queued", time.time()),
            )
        return ident

    def jobs(self):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created")]

    def update_job(self, ident, status, progress=0, error=""):
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status=?,progress=?,error=? WHERE id=?", (status, progress, error, ident)
            )

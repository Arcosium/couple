import sqlite3
import threading
from contextlib import contextmanager
from .config import settings

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_conn = _connect()


@contextmanager
def cursor():
    with _lock:
        cur = _conn.cursor()
        try:
            yield cur
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise
        finally:
            cur.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    nickname TEXT,
    avatar TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    expires_ts INTEGER NOT NULL,
    used INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_login_codes ON login_codes (email, code);

CREATE TABLE IF NOT EXISTS settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    owner_email TEXT NOT NULL,
    filename TEXT NOT NULL,
    caption TEXT,
    place_name TEXT,
    lat REAL,
    lng REAL,
    taken_at TEXT,
    uploaded_at TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_photos_time ON photos (taken_at);
CREATE INDEX IF NOT EXISTS idx_photos_place ON photos (place_name);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    due TEXT,
    end_date TEXT,
    time TEXT,
    note TEXT,
    color TEXT,
    source TEXT DEFAULT 'calendar',
    done INTEGER DEFAULT 0,
    reminder_minutes INTEGER,
    notified_ts REAL,
    owner_email TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_due ON events (due);

CREATE TABLE IF NOT EXISTS bucket (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    icon TEXT,
    target_date TEXT,
    priority INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    done_at TEXT,
    created_at TEXT NOT NULL,
    owner_email TEXT
);

CREATE TABLE IF NOT EXISTS places (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    address TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    kind TEXT NOT NULL,          -- 'visited' | 'wishlist'
    category TEXT,                -- 음식점/카페/여행/데이트 등
    rating INTEGER,
    memo TEXT,
    visited_at TEXT,
    created_at TEXT NOT NULL,
    owner_email TEXT
);

CREATE TABLE IF NOT EXISTS pokes (
    id TEXT PRIMARY KEY,
    from_email TEXT NOT NULL,
    to_email TEXT NOT NULL,
    emoji TEXT NOT NULL,
    message TEXT,
    seen INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pokes_to ON pokes (to_email, seen);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,           -- 'user' | 'assistant'
    content TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_email TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages (session_id, created_at);
"""


def _migrate() -> None:
    """기존 테이블에 새 컬럼을 멱등하게 추가(SCHEMA 의 IF NOT EXISTS 로는 컬럼 추가가 안 됨)."""
    migrations = [
        ("events", "end_date", "TEXT"),   # 2일 이상 이어지는 일정의 종료일(선택)
    ]
    with cursor() as cur:
        for table, col, coltype in migrations:
            cols = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
    _migrate()


def kv_get(key: str, default: str | None = None) -> str | None:
    with cursor() as cur:
        row = cur.execute("SELECT value FROM settings_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings_kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


init_db()

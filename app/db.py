import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
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
    last_seen TEXT,
    username TEXT,
    password_hash TEXT
);
-- NOTE: username 부분 유니크 인덱스는 _migrate() 에서 생성한다.
-- 기존 DB 는 users 테이블이 이미 있어 위 CREATE 가 no-op → username 컬럼이
-- ALTER(_migrate) 전엔 없으므로, 여기서 인덱스를 만들면 'no such column' 으로 죽는다.

CREATE TABLE IF NOT EXISTS login_codes (
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    expires_ts INTEGER NOT NULL,
    used INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_login_codes ON login_codes (email, code);

CREATE TABLE IF NOT EXISTS settings_kv (
    couple_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (couple_id, key)
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
    size_bytes INTEGER,
    tags TEXT
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

CREATE TABLE IF NOT EXISTS couples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_a TEXT NOT NULL,
    member_b TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS couple_invites (
    id TEXT PRIMARY KEY,
    inviter_email TEXT NOT NULL,
    invitee_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    responded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invites_invitee ON couple_invites (invitee_email, status);
CREATE INDEX IF NOT EXISTS idx_invites_inviter ON couple_invites (inviter_email, status);

CREATE TABLE IF NOT EXISTS daily_notes (
    id TEXT PRIMARY KEY,
    couple_id INTEGER NOT NULL,
    author_email TEXT NOT NULL,
    date TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (couple_id, author_email, date)
);
CREATE INDEX IF NOT EXISTS idx_notes_couple_date ON daily_notes (couple_id, date);
"""


def _bootstrap_couple_one() -> None:
    """레거시 단일커플 데이터를 couple #1 로 멱등 승격.
    - couples 가 비었고 레거시(couple_id NULL) 데이터가 있으면 ALLOWED_EMAILS 2명으로 #1 생성.
    - couple #1 이 있으면 NULL 인 모든 데이터/유저/kv 를 1 로 백필."""
    emails = settings.allowed_emails
    with cursor() as cur:
        has_one = cur.execute("SELECT 1 FROM couples WHERE id=1").fetchone()
        legacy = cur.execute(
            "SELECT 1 FROM events WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM places WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM bucket WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM photos WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM pokes WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM chat_messages WHERE couple_id IS NULL LIMIT 1"
        ).fetchone()
        if not has_one and legacy and len(emails) < 2:
            print("[migrate] WARNING: 레거시 데이터가 있으나 ALLOWED_EMAILS<2 라 couple #1 미생성 "
                  "— 데이터가 미매칭으로 남음. ALLOWED_EMAILS 설정 후 재기동 필요.", flush=True)
        if not has_one and legacy and len(emails) >= 2:
            cur.execute("INSERT INTO couples (id, member_a, member_b, created_at) "
                        "VALUES (1, ?, ?, ?)",
                        (emails[0], emails[1], datetime.now().isoformat(timespec="seconds")))
            has_one = True
        if has_one:
            for table in ("events", "places", "bucket", "photos", "pokes", "chat_messages"):
                cur.execute(f"UPDATE {table} SET couple_id=1 WHERE couple_id IS NULL")
            for em in emails[:2]:
                cur.execute("INSERT INTO users (email, couple_id) VALUES (?, 1) "
                            "ON CONFLICT(email) DO UPDATE SET couple_id=1", (em,))


def _rebuild_settings_kv(cur) -> None:
    """settings_kv 를 (couple_id, key) 복합키로 멱등·자가복구 전환.
    Python 3.11 sqlite3 는 DDL 을 즉시 자동커밋하므로 RENAME→CREATE→INSERT→DROP 가 비원자적이다.
    크래시로 settings_kv(빈 새 테이블) + settings_kv_old(실데이터)가 남는 'torn state' 를
    다음 기동에서 복구한다."""
    has_old = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings_kv_old'").fetchone()
    kv_cols = {r["name"] for r in cur.execute("PRAGMA table_info(settings_kv)").fetchall()}
    if "couple_id" in kv_cols and not has_old:
        return  # 이미 복합키 + 잔재 없음 → 할 일 없음
    if not has_old:
        # 정상 레거시 전환: 현재(단일PK) settings_kv 를 백업으로 rename
        cur.execute("ALTER TABLE settings_kv RENAME TO settings_kv_old")
    # 새 복합키 테이블 보장 (torn state 의 빈 테이블이면 버리고 재생성)
    cur.execute("DROP TABLE IF EXISTS settings_kv")
    cur.execute("CREATE TABLE settings_kv (couple_id INTEGER NOT NULL, key TEXT NOT NULL, "
                "value TEXT, PRIMARY KEY (couple_id, key))")
    cur.execute("INSERT OR IGNORE INTO settings_kv (couple_id, key, value) "
                "SELECT 1, key, value FROM settings_kv_old")
    cur.execute("DROP TABLE settings_kv_old")


def _migrate() -> None:
    """멱등 마이그레이션: couple_id 컬럼·end_date·settings_kv 복합키 + couple #1 부트스트랩."""
    with cursor() as cur:
        # 1) couple_id 컬럼 (멱등)
        for table in ("users", "events", "places", "bucket", "photos", "pokes", "chat_messages"):
            cols = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if "couple_id" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN couple_id INTEGER")
        # users 에 username/password_hash (멱등) + 부분 유니크 인덱스
        user_cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
        for col in ("username", "password_hash"):
            if col not in user_cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username "
                    "ON users (username) WHERE username IS NOT NULL")
        # photos.tags (자동 태깅, 쉼표 구분)
        if "tags" not in {r["name"] for r in cur.execute("PRAGMA table_info(photos)").fetchall()}:
            cur.execute("ALTER TABLE photos ADD COLUMN tags TEXT")
        # events.end_date (기존 마이그레이션 유지)
        ev_cols = {r["name"] for r in cur.execute("PRAGMA table_info(events)").fetchall()}
        if "end_date" not in ev_cols:
            cur.execute("ALTER TABLE events ADD COLUMN end_date TEXT")
        # 2) settings_kv 단일PK → (couple_id,key) 복합키 재작성 (멱등·자가복구)
        _rebuild_settings_kv(cur)
    _bootstrap_couple_one()


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
    _migrate()


def kv_get(couple_id: int, key: str, default: str | None = None) -> str | None:
    with cursor() as cur:
        row = cur.execute("SELECT value FROM settings_kv WHERE couple_id=? AND key=?",
                          (couple_id, key)).fetchone()
        return row["value"] if row else default


def kv_set(couple_id: int, key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings_kv (couple_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(couple_id, key) DO UPDATE SET value=excluded.value",
            (couple_id, key, value),
        )


def couple_members(couple_id: int) -> list[str]:
    with cursor() as cur:
        row = cur.execute("SELECT member_a, member_b FROM couples WHERE id=?",
                          (couple_id,)).fetchone()
    return [row["member_a"], row["member_b"]] if row else []


init_db()

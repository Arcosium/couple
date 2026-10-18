import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from .config import settings
from .crypto import dec_text, enc_text

_lock = threading.Lock()


class _Row(dict):
    """sqlite3.Row 처럼 이름·순서 둘 다로 읽힌다."""
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else super().__getitem__(k)


def _row(cur, row) -> _Row:
    """모든 SELECT 결과를 여기서 복호화한다(평문은 그대로 통과). 쓰기는 각 저장 지점에서 enc_text."""
    return _Row((d[0], dec_text(v)) for d, v in zip(cur.description, row))


# 암호화해 저장하는 본문 컬럼. 검색·중복 판정에 쓰는 이름·주소·캡션·태그는 평문으로 둔다.
ENCRYPTED_COLUMNS = {
    "chat_messages": ("content",),
    "daily_notes": ("content",),
    "events": ("title", "note"),
    "bucket": ("title", "description"),
    "places": ("memo",),
    "pokes": ("message",),
    "settings_kv": ("value",),
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, check_same_thread=False, timeout=10)
    conn.row_factory = _row
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
        created = False
        # 이미 다른 커플에 묶인 계정이 있으면 #1 을 만들지 않는다(현재 커플에서 빼 오는 사고 방지).
        bound = cur.execute(
            f"SELECT 1 FROM users WHERE couple_id IS NOT NULL AND email IN ({','.join('?' * len(emails[:2]))})",
            emails[:2]).fetchone() if emails else None
        if not has_one and legacy and len(emails) >= 2 and not bound:
            cur.execute("INSERT INTO couples (id, member_a, member_b, created_at) "
                        "VALUES (1, ?, ?, ?)",
                        (emails[0], emails[1], datetime.now().isoformat(timespec="seconds")))
            has_one = created = True
        if has_one:
            for table in ("events", "places", "bucket", "photos", "pokes", "chat_messages"):
                cur.execute(f"UPDATE {table} SET couple_id=1 WHERE couple_id IS NULL")
        # 유저 행 보장은 #1 을 처음 만들 때만. 매 기동마다 하면 ALLOWED_EMAILS 변경 시
        # 비번 없는 행이 couple #1 에 묶여 생기고, 해제한 #1 을 강제로 다시 묶는다.
        if created:
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
        # session_key: 로그아웃·비번 변경 시 교체 → 그 전에 발급된 쿠키 전부 무효
        for col in ("username", "password_hash", "session_key"):
            if col not in user_cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        # couples.ended_at: 해제해도 행을 남겨 같은 두 사람이 다시 연결하면 데이터를 되살린다
        if "ended_at" not in {r["name"] for r in cur.execute("PRAGMA table_info(couples)").fetchall()}:
            cur.execute("ALTER TABLE couples ADD COLUMN ended_at TEXT")
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


def encrypt_existing() -> int:
    """평문으로 남은 본문 컬럼을 암호화(멱등). 바꾼 값 개수 반환."""
    n = 0
    with cursor() as cur:
        for table, cols in ENCRYPTED_COLUMNS.items():
            for col in cols:
                rows = cur.execute(
                    f"SELECT rowid AS rid, {col} AS v FROM {table} "
                    f"WHERE {col} IS NOT NULL AND {col} != '' AND {col} NOT LIKE 'enc1:%'").fetchall()
                for r in rows:
                    cur.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (enc_text(r["v"]), r["rid"]))
                n += len(rows)
    return n


def init_db() -> None:
    with cursor() as cur:
        cur.executescript(SCHEMA)
    _migrate()
    encrypt_existing()


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
            (couple_id, key, enc_text(value)),
        )


def couple_members(couple_id: int) -> list[str]:
    with cursor() as cur:
        row = cur.execute("SELECT member_a, member_b FROM couples WHERE id=? AND ended_at IS NULL",
                          (couple_id,)).fetchone()
    return [row["member_a"], row["member_b"]] if row else []


init_db()

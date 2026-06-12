"""스키마/마이그레이션: 신규 테이블·couple_id·settings_kv 복합키·couple #1 백필."""
import sqlite3
from app import db


def _legacy_db(path):
    """레거시(단일PK settings_kv, couple_id 없음) DB 를 손으로 만든다."""
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE settings_kv (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE users (email TEXT PRIMARY KEY, nickname TEXT, avatar TEXT, last_seen TEXT);"
        "CREATE TABLE events (id TEXT PRIMARY KEY, title TEXT, created_at TEXT);"
        "CREATE TABLE places (id TEXT PRIMARY KEY, created_at TEXT);"
        "CREATE TABLE bucket (id TEXT PRIMARY KEY, title TEXT, created_at TEXT);"
        "CREATE TABLE photos (id TEXT PRIMARY KEY, owner_email TEXT, filename TEXT, uploaded_at TEXT);"
        "CREATE TABLE pokes (id TEXT PRIMARY KEY, from_email TEXT, to_email TEXT, emoji TEXT, created_at TEXT);"
        "CREATE TABLE chat_messages (id TEXT PRIMARY KEY, role TEXT, content TEXT, session_id TEXT, created_at TEXT);"
        "CREATE TABLE couples (id INTEGER PRIMARY KEY AUTOINCREMENT, member_a TEXT, member_b TEXT, created_at TEXT);"
        "INSERT INTO settings_kv (key, value) VALUES ('theme','rosy'),('mascot','bunny');"
    )
    c.commit(); c.close()


def test_legacy_settings_kv_rebuilds_preserving_rows(monkeypatch, tmp_path):
    p = str(tmp_path / "legacy.db")
    _legacy_db(p)
    conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row
    db._rebuild_settings_kv(conn.cursor()); conn.commit()
    cur = conn.cursor()
    cols = {r["name"] for r in cur.execute("PRAGMA table_info(settings_kv)").fetchall()}
    assert "couple_id" in cols
    rows = {(r["couple_id"], r["key"], r["value"])
            for r in cur.execute("SELECT couple_id,key,value FROM settings_kv").fetchall()}
    assert (1, "theme", "rosy") in rows and (1, "mascot", "bunny") in rows
    assert not cur.execute("SELECT 1 FROM sqlite_master WHERE name='settings_kv_old'").fetchone()
    conn.close()


def test_torn_state_recovers_from_settings_kv_old(tmp_path):
    """settings_kv(빈 새 테이블) + settings_kv_old(실데이터) → 복구."""
    p = str(tmp_path / "torn.db")
    conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE settings_kv (couple_id INTEGER NOT NULL, key TEXT, value TEXT, PRIMARY KEY(couple_id,key));"
        "CREATE TABLE settings_kv_old (key TEXT PRIMARY KEY, value TEXT);"
        "INSERT INTO settings_kv_old (key,value) VALUES ('theme','mint');"
    )
    conn.commit()
    db._rebuild_settings_kv(conn.cursor()); conn.commit()
    cur = conn.cursor()
    rows = {(r["couple_id"], r["key"], r["value"])
            for r in cur.execute("SELECT couple_id,key,value FROM settings_kv").fetchall()}
    assert (1, "theme", "mint") in rows
    assert not cur.execute("SELECT 1 FROM sqlite_master WHERE name='settings_kv_old'").fetchone()
    conn.close()


def _table_cols(table):
    with db.cursor() as cur:
        return {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def test_new_tables_exist():
    with db.cursor() as cur:
        names = {r["name"] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"couples", "couple_invites"} <= names


def test_couple_id_columns_added():
    for t in ("users", "events", "places", "bucket", "photos", "pokes", "chat_messages"):
        assert "couple_id" in _table_cols(t), t


def test_settings_kv_is_composite():
    cols = _table_cols("settings_kv")
    assert "couple_id" in cols and "key" in cols and "value" in cols


def test_bootstrap_promotes_legacy_rows_to_couple_one():
    # 레거시 행(couple_id NULL) 삽입 → 부트스트랩이 1 로 승격
    with db.cursor() as cur:
        cur.execute("INSERT INTO bucket (id, title, created_at) VALUES ('lg1', 'legacy', '2026-01-01')")
    db._bootstrap_couple_one()
    with db.cursor() as cur:
        row = cur.execute("SELECT couple_id FROM bucket WHERE id='lg1'").fetchone()
    assert row["couple_id"] == 1


def test_kv_is_per_couple():
    db.kv_set(1, "theme", "rosy")
    db.kv_set(2, "theme", "mint")
    assert db.kv_get(1, "theme") == "rosy"
    assert db.kv_get(2, "theme") == "mint"
    assert db.kv_get(3, "theme", "default") == "default"


def test_couple_members_returns_both():
    with db.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                    "VALUES (9, 'x@t', 'y@t', '2026-01-01')")
    assert set(db.couple_members(9)) == {"x@t", "y@t"}
    assert db.couple_members(999) == []

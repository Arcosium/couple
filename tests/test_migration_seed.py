"""스키마/마이그레이션: 신규 테이블·couple_id·settings_kv 복합키·couple #1 백필."""
from app import db


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

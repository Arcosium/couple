from app.db import cursor


def test_users_has_new_columns():
    with cursor() as cur:
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    assert "username" in cols
    assert "password_hash" in cols


def test_username_partial_unique_index_exists():
    with cursor() as cur:
        idx = {r["name"] for r in cur.execute("PRAGMA index_list(users)").fetchall()}
    assert "idx_users_username" in idx


def test_multiple_null_usernames_allowed():
    # 레거시 미claim 행이 여러 개여도(username=NULL) 충돌 없어야 한다.
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('legacy1@test')")
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('legacy2@test')")
        n = cur.execute(
            "SELECT COUNT(*) c FROM users WHERE email IN ('legacy1@test','legacy2@test')"
        ).fetchone()["c"]
    assert n == 2

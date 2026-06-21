"""기존 DB(레거시 users 테이블, username 컬럼 없음)에서 init_db 가 죽지 않아야 한다.

회귀 방지: SCHEMA 의 username 인덱스 DDL 이 _migrate 의 ALTER 보다 먼저 실행돼
기존 DB 에서 'no such column: username' 으로 죽던 버그(2026-06-21 운영 크래시).
fresh DB(다른 테스트)는 CREATE TABLE 에 username 이 있어 이 경로를 안 탔다.
"""
import os
import sqlite3
import subprocess
import sys


def test_init_db_on_existing_legacy_users_table(tmp_path):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE users (email TEXT PRIMARY KEY, nickname TEXT, avatar TEXT, last_seen TEXT);"
        "INSERT INTO users (email) VALUES ('legacy@x.com');"
    )
    con.commit()
    con.close()
    # 새 인터프리터에서 COUPLE_DB 를 레거시 DB 로 두고 app.db import → init_db 실행
    env = {**os.environ, "COUPLE_DB": str(db)}
    check = (
        "import app.db\n"
        "from app.db import cursor\n"
        "with cursor() as cur:\n"
        "    cols={x['name'] for x in cur.execute('PRAGMA table_info(users)').fetchall()}\n"
        "    idx={x['name'] for x in cur.execute('PRAGMA index_list(users)').fetchall()}\n"
        "assert 'username' in cols and 'password_hash' in cols, cols\n"
        "assert 'idx_users_username' in idx, idx\n"
        "print('OK')\n"
    )
    r = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"init_db crashed on legacy DB:\n{r.stderr}"
    assert "OK" in r.stdout

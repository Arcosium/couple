"""테스트 격리: 임시 DB 로 운영 couple.db 를 절대 건드리지 않는다.
server/app import 전에 env 를 박아야 config·db 가 임시 경로를 집는다."""
import os
import sqlite3
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="couple-test-"), "test.db")
os.environ["COUPLE_DB"] = _TMP_DB
os.environ["OPEN_SIGNUP"] = "1"

import pytest
from app.config import settings
from app import db as _db


@pytest.fixture(scope="session", autouse=True)
def _seed_couple_one():
    """allowed_emails 2명으로 couple #1 시드 — 기존 테스트(CF Access 헤더)가
    require_couple 게이트를 통과하도록. couples 테이블이 아직 없을 수 있으니 방어."""
    a, b = (settings.allowed_emails + ["a@test", "b@test"])[:2]
    try:
        with _db.cursor() as cur:
            cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                        "VALUES (1, ?, ?, '2026-01-01')", (a, b))
            for em in (a, b):
                cur.execute("INSERT INTO users (email, couple_id) VALUES (?, 1) "
                            "ON CONFLICT(email) DO UPDATE SET couple_id=1", (em,))
    except sqlite3.OperationalError:
        pass  # couples/couple_id 미생성 단계 — 후속 Phase 에서 생성됨
    yield

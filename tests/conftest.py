"""테스트 격리: 임시 DB 로 운영 couple.db 를 절대 건드리지 않는다.
server/app import 전에 env 를 박아야 config·db 가 임시 경로를 집는다."""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="couple-test-"), "test.db")
os.environ["COUPLE_DB"] = _TMP_DB
os.environ["OPEN_SIGNUP"] = "1"
os.environ.setdefault("ALLOWED_EMAILS", "a@test,b@test")

import pytest
from app.config import settings
from app import db as _db


@pytest.fixture(scope="session", autouse=True)
def _seed_couple_one():
    """allowed_emails 2명으로 couple #1 시드 — 기존 테스트(CF Access 헤더)가
    require_couple 게이트를 통과하도록. Phase 1 이후 couples/couple_id 가 보장되므로
    예외 스왈로 없이 실행한다(시드 버그를 가리지 않도록)."""
    a, b = (settings.allowed_emails + ["a@test", "b@test"])[:2]
    with _db.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                    "VALUES (1, ?, ?, '2026-01-01')", (a, b))
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, 1) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=1", (em,))
    yield

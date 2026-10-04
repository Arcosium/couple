from fastapi.testclient import TestClient
from server import app
from app import db
from app.config import settings as cfg
from app.auth import SESSION_COOKIE, make_session_cookie

client = TestClient(app)


def _h(e): return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(e)}"}


def test_unmatched_sees_match_screen():
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES ('m1@t', NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL")
    html = client.get("/", headers=_h("m1@t")).text
    assert "커플 초대하기" in html
    assert "인증 코드 받기" not in html


def test_matched_sees_app():
    html = client.get("/", headers=_h(cfg.allowed_emails[0])).text
    assert "커플 초대하기" not in html

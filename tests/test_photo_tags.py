"""사진 자동 태깅: 모델 답변 파서 + PATCH 로 태그 추가/삭제.
LLM 호출은 하지 않는다(파서만 검증 + 업로드 시 태깅 enqueue 는 no-op 으로 대체)."""
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import app
from app import db, couples
from app.auth import SESSION_COOKIE, make_session_cookie
from app.photo_tagger import parse_tags
from app.routes import photos_routes as pr

client = TestClient(app)


def _h(e): return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(e)}"}


def _match(a, b):
    with db.cursor() as cur:
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (em,))
    inv = couples.create_invite(a, b); couples.accept_invite(b, inv["id"])


@pytest.fixture(autouse=True)
def _tmp_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(pr.settings, "uploads_dir", tmp_path / "photos")
    monkeypatch.setattr(pr, "THUMBS_DIR", tmp_path / "photos" / "thumbs")
    monkeypatch.setattr(pr, "enqueue_tagging", lambda *a, **k: None)   # 테스트는 LLM 안 부른다
    yield


def test_parse_plain():
    assert parse_tags("카페, 디저트, 데이트") == ["카페", "디저트", "데이트"]


def test_parse_strips_chatter_and_think():
    text = "<think>음 사진을 보면…</think>\n알겠어! 태그는 다음과 같아:\n#바다, #노을, 여행\n"
    assert parse_tags(text) == ["바다", "노을", "여행"]


def test_parse_caps_and_dedupes():
    assert parse_tags("a, a, b, c, d, e, f, g") == ["a", "b", "c", "d", "e", "f"]
    assert parse_tags("정상, " + "가" * 30) == ["정상"]   # 너무 긴 건 문장이지 태그가 아니다


def test_parse_junk_is_empty():
    assert parse_tags("") == []


def test_patch_tags_add_remove_and_search():
    """리스트로 보낸 태그가 저장되고, 검색에 걸리고, 빈 리스트로 전부 지워진다."""
    _match("tag1@t", "tag2@t")
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 120, 120)).save(buf, "JPEG")
    pid = client.post("/api/photos/upload", files={"file": ("t.jpg", buf.getvalue(), "image/jpeg")},
                      headers=_h("tag1@t")).json()["id"]

    def _row():
        return next(p for p in client.get("/api/photos", headers=_h("tag1@t")).json()
                    if p["id"] == pid)

    assert client.patch(f"/api/photos/{pid}", json={"tags": ["바다", " 노을 ", ""]},
                        headers=_h("tag1@t")).status_code == 200
    assert _row()["tags"] == "바다,노을"
    # 태그로 검색되고, 상대방도 같은 태그를 본다
    hits = client.get("/api/photos?q=노을", headers=_h("tag2@t")).json()
    assert [p["id"] for p in hits] == [pid]

    assert client.patch(f"/api/photos/{pid}", json={"tags": []},
                        headers=_h("tag1@t")).status_code == 200
    assert _row()["tags"] == ""

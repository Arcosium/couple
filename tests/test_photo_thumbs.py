"""사진 썸네일: 업로드 시 축소 저장 + 썸네일 생성, 조회는 원본/썸네일 분리, 삭제 시 둘 다 정리.
uploads_dir 은 반드시 tmp 로 갈아끼운다 — 안 그러면 운영 uploads/photos 에 쓴다."""
import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import app
from app import db, couples
from app.auth import SESSION_COOKIE, make_session_cookie
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
    yield


def _jpeg(w, h) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 80, 120)).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_big_upload_is_downscaled_and_thumbed():
    _match("ph1@t", "ph2@t")
    big = _jpeg(4000, 3000)
    r = client.post("/api/photos/upload", files={"file": ("a.jpg", big, "image/jpeg")},
                    headers=_h("ph1@t"))
    assert r.status_code == 200
    pid = r.json()["id"]

    # 원본은 긴 변 MAX_SIDE 이하로 줄어 용량도 작아진다
    stored = pr.settings.uploads_dir / f"{pid}.jpg"
    with Image.open(stored) as im:
        assert max(im.size) <= pr.MAX_SIDE
    assert stored.stat().st_size < len(big)

    # 썸네일은 업로드 시점에 이미 만들어져 있고 원본보다 훨씬 작다
    tp = pr._thumb_path(pid)
    assert tp.exists() and tp.stat().st_size < stored.stat().st_size
    with Image.open(tp) as im:
        assert max(im.size) <= pr.THUMB_SIDE

    # 목록/조회 배선
    row = next(x for x in client.get("/api/photos", headers=_h("ph1@t")).json() if x["id"] == pid)
    assert row["thumb_url"].endswith("?thumb=1")
    full = client.get(f"/api/photos/file/{pid}", headers=_h("ph1@t"))
    thumb = client.get(f"/api/photos/file/{pid}?thumb=1", headers=_h("ph1@t"))
    assert full.status_code == thumb.status_code == 200
    assert len(thumb.content) < len(full.content)
    assert "immutable" in thumb.headers["cache-control"]


def test_thumb_is_generated_on_demand_for_old_photos():
    _match("ph3@t", "ph4@t")
    pid = client.post("/api/photos/upload", files={"file": ("b.jpg", _jpeg(900, 900), "image/jpeg")},
                      headers=_h("ph3@t")).json()["id"]
    pr._thumb_path(pid).unlink()                      # 썸네일 없던 기존 사진 흉내
    assert client.get(f"/api/photos/file/{pid}?thumb=1", headers=_h("ph3@t")).status_code == 200
    assert pr._thumb_path(pid).exists()


def test_delete_removes_thumb_too():
    _match("ph5@t", "ph6@t")
    pid = client.post("/api/photos/upload", files={"file": ("c.jpg", _jpeg(900, 900), "image/jpeg")},
                      headers=_h("ph5@t")).json()["id"]
    assert client.delete(f"/api/photos/{pid}", headers=_h("ph5@t")).status_code == 200
    assert not pr._thumb_path(pid).exists()
    assert not (pr.settings.uploads_dir / f"{pid}.jpg").exists()

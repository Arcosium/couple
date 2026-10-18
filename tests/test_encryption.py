"""저장 데이터 암호화: DB 원문·사진 파일에는 암호문만, API 로는 평문. 옛 평문은 이관·통과."""
import io
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image

from server import app
from app import crypto, db
from app.config import settings

PW = "pw12345678"


def _pair(a, b):
    ca, cb = TestClient(app), TestClient(app)
    for c, u in ((ca, a), (cb, b)):
        assert c.post("/api/auth/signup", json={"username": u, "password": PW}).status_code == 200
    iid = ca.post("/api/couple/invite", json={"email": b}).json()["id"]
    cb.post(f"/api/couple/invites/{iid}/accept")
    return ca, cb


def _raw(sql, args=()):
    c = sqlite3.connect(settings.db_path)          # row_factory 없이 디스크 원문 그대로
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def test_text_roundtrip_and_passthrough():
    e = crypto.enc_text("비밀 메모")
    assert e.startswith("enc1:") and e != crypto.enc_text("비밀 메모")      # 매번 다른 nonce
    assert crypto.dec_text(e) == "비밀 메모"
    assert crypto.dec_text("평문") == "평문" and crypto.dec_text("enc1:깨진값") == "enc1:깨진값"
    assert crypto.enc_text("") == "" and crypto.enc_text(None) is None


def test_db_stores_ciphertext_api_returns_plaintext():
    ca, cb = _pair("enc_a1", "enc_b1")
    ca.post("/api/events", json={"title": "산부인과 예약", "due": "2030-01-01", "note": "3층"})
    ca.post("/api/bucket", json={"title": "제주 여행", "description": "4월"})
    ca.post("/api/places", json={"name": "카페", "lat": 37.5, "lng": 127.0, "memo": "창가 자리"})
    ca.put("/api/notes", json={"date": "2030-01-01", "content": "오늘 좋았어"})
    ca.post("/api/pokes", json={"emoji": "👉", "message": "보고싶어"})
    ca.patch("/api/settings", json={"nickname_a": "곰돌이", "birthday_a": "1999-05-05"})
    secrets_ = ["산부인과 예약", "3층", "제주 여행", "4월", "창가 자리", "오늘 좋았어", "보고싶어", "곰돌이", "1999-05-05"]
    dump = "\n".join(str(r) for t in ("events", "bucket", "places", "daily_notes", "pokes", "settings_kv")
                     for r in _raw(f"SELECT * FROM {t}"))
    assert not [s for s in secrets_ if s in dump]
    assert "enc1:" in dump
    assert any(e["title"] == "산부인과 예약" and e["note"] == "3층" for e in cb.get("/api/events").json())
    assert any(b["description"] == "4월" for b in cb.get("/api/bucket").json())
    assert any(p["memo"] == "창가 자리" for p in cb.get("/api/places").json())
    assert cb.get("/api/notes?date=2030-01-01").json()["partner"]["content"] == "오늘 좋았어"
    assert any(p["message"] == "보고싶어" for p in cb.get("/api/pokes").json())
    assert cb.get("/api/settings").json()["nickname_a"] == "곰돌이"
    # 수정 경로도 암호화
    eid = next(e["id"] for e in ca.get("/api/events").json() if e["title"] == "산부인과 예약")
    ca.patch(f"/api/events/{eid}", json={"note": "4층으로 변경"})
    assert "4층으로 변경" not in str(_raw("SELECT note FROM events WHERE id=?", (eid,)))


def test_photo_file_encrypted_on_disk_plain_over_api():
    ca, _ = _pair("enc_a2", "enc_b2")
    buf = io.BytesIO(); Image.new("RGB", (64, 64), (10, 200, 30)).save(buf, "JPEG")
    pid = ca.post("/api/photos/upload", files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")}).json()["id"]
    disk = (settings.uploads_dir / f"{pid}.jpg").read_bytes()
    assert disk.startswith(crypto._MAGIC) and b"JFIF" not in disk[:64]
    r = ca.get(f"/api/photos/file/{pid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    Image.open(io.BytesIO(r.content)).verify()
    t = ca.get(f"/api/photos/file/{pid}?thumb=1")
    assert t.status_code == 200 and t.content[:2] == b"\xff\xd8"


def test_existing_plaintext_is_migrated_idempotently(tmp_path):
    with db.cursor() as cur:
        cur.execute("INSERT INTO chat_messages (id, role, content, session_id, created_at, couple_id) "
                    "VALUES ('enc-old', 'user', '옛 평문 대화', 's', 'x', 999)")
    assert db.encrypt_existing() >= 1
    assert _raw("SELECT content FROM chat_messages WHERE id='enc-old'")[0][0].startswith("enc1:")
    assert db.encrypt_existing() == 0
    with db.cursor() as cur:
        assert cur.execute("SELECT content FROM chat_messages WHERE id='enc-old'").fetchone()["content"] == "옛 평문 대화"
        cur.execute("DELETE FROM chat_messages WHERE id='enc-old'")
    p = tmp_path / "old.jpg"; p.write_bytes(b"\xff\xd8plain")
    assert crypto.encrypt_dir(tmp_path) == 1 and crypto.encrypt_dir(tmp_path) == 0
    assert crypto.read_file(p) == b"\xff\xd8plain"

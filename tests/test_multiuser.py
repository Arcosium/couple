"""공개 가입 다중 사용자 회귀: 아이디 초대·기본값 격리·세션 회수·재연결·탈퇴·자원 상한."""
import io

from fastapi.testclient import TestClient
from PIL import Image

from server import app
from app import auth, couples
from app.auth import SESSION_COOKIE
from app.db import cursor

PW = "pw12345678"


def _signup(u):
    c = TestClient(app)
    assert c.post("/api/auth/signup", json={"username": u, "password": PW}).status_code == 200
    return c


def _pair(a, b):
    ca, cb = _signup(a), _signup(b)
    iid = ca.post("/api/couple/invite", json={"email": b}).json()["id"]
    assert cb.post(f"/api/couple/invites/{iid}/accept").status_code == 200
    return ca, cb


def _cookie_client(tok):
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, tok)
    return c


def test_invite_by_username_and_list_shows_username():
    ca, cb = _signup("mu_a1"), _signup("mu_b1")
    r = ca.post("/api/couple/invite", json={"email": "MU_B1"})
    assert r.status_code == 200 and r.json()["invitee_email"] == "mu_b1"
    inc = cb.get("/api/couple/invites").json()["incoming"]
    assert inc[0]["inviter_username"] == "mu_a1"
    assert cb.post(f"/api/couple/invites/{inc[0]['id']}/accept").status_code == 200
    assert ca.get("/api/couple/status").json()["partner_email"] == "mu_b1"


def test_invite_unknown_user_400():
    ca = _signup("mu_a2")
    r = ca.post("/api/couple/invite", json={"email": "nobody_here"})
    assert r.status_code == 400 and r.json()["detail"] == "user_not_found"


def test_new_couple_gets_neutral_defaults_and_no_auto_events():
    ca, _ = _pair("mu_a3", "mu_b3")
    s = ca.get("/api/settings").json()
    assert s["anniversary_date"] == "" and s["birthday_a"] == "" and s["birthday_b"] == ""
    assert (s["nickname_a"], s["nickname_b"]) == ("자기", "애기")
    from app.special_events import ensure_special_events
    assert ensure_special_events(auth.couple_of("mu_a3")) == 0
    assert ca.get("/api/auth/me").json()["needs_onboarding"] is True


def test_health_hides_online_users():
    assert TestClient(app).get("/health").json() == {"ok": True}


def test_logout_revokes_all_copies_of_session():
    c = _signup("mu_a4")
    tok = c.cookies.get(SESSION_COOKIE)
    other = TestClient(app)
    other.post("/api/auth/login", json={"username": "mu_a4", "password": PW})
    c.post("/api/auth/logout")
    assert _cookie_client(tok).get("/api/auth/me").json()["authenticated"] is False
    assert other.get("/api/auth/me").json()["authenticated"] is False


def test_password_change_keeps_this_device_only():
    c = _signup("mu_a5")
    old_tok = c.cookies.get(SESSION_COOKIE)
    bad = c.post("/api/auth/password", json={"current_password": "wrong!!!", "new_password": "newpw12345"})
    assert bad.status_code == 400
    assert c.post("/api/auth/password",
                  json={"current_password": PW, "new_password": "newpw12345"}).status_code == 200
    assert c.get("/api/auth/me").json()["authenticated"] is True
    assert _cookie_client(old_tok).get("/api/auth/me").json()["authenticated"] is False
    assert auth.authenticate("mu_a5", PW) is None
    assert auth.authenticate("mu_a5", "newpw12345") == "mu_a5"


def test_relink_same_pair_restores_data_other_pair_does_not():
    ca, cb = _pair("mu_a6", "mu_b6")
    ca.post("/api/bucket", json={"title": "제주"})
    old = auth.couple_of("mu_a6")
    ca.post("/api/couple/unlink")
    cc = _signup("mu_c6")
    iid = ca.post("/api/couple/invite", json={"email": "mu_c6"}).json()["id"]
    cc.post(f"/api/couple/invites/{iid}/accept")
    assert ca.get("/api/bucket").json() == []                 # 새 상대에게는 안 보인다
    ca.post("/api/couple/unlink")
    iid = cb.post("/api/couple/invite", json={"email": "mu_a6"}).json()["id"]
    ca.post(f"/api/couple/invites/{iid}/accept")
    assert auth.couple_of("mu_a6") == old
    assert [b["title"] for b in cb.get("/api/bucket").json()] == ["제주"]


def test_unlinked_couple_gets_no_reminder_members():
    from app.db import couple_members
    ca, _ = _pair("mu_a7", "mu_b7")
    cid = auth.couple_of("mu_a7")
    ca.post("/api/couple/unlink")
    assert couple_members(cid) == []


def test_delete_account_purges_couple_and_frees_username():
    ca, cb = _pair("mu_a8", "mu_b8")
    cid = auth.couple_of("mu_a8")
    ca.post("/api/events", json={"title": "secret"})
    buf = io.BytesIO(); Image.new("RGB", (8, 8)).save(buf, "JPEG")
    pid = ca.post("/api/photos/upload", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")}).json()["id"]
    from app.config import settings
    assert (settings.uploads_dir / f"{pid}.jpg").exists()
    tok = ca.cookies.get(SESSION_COOKIE)
    assert ca.post("/api/auth/delete_account", json={"password": "wrong!!!"}).status_code == 400
    assert ca.post("/api/auth/delete_account", json={"password": PW}).status_code == 200
    with cursor() as cur:
        assert cur.execute("SELECT 1 FROM users WHERE email='mu_a8'").fetchone() is None
        for t in couples.COUPLE_TABLES + ("couples",):
            col = "id" if t == "couples" else "couple_id"
            assert cur.execute(f"SELECT COUNT(*) n FROM {t} WHERE {col}=?", (cid,)).fetchone()["n"] == 0
    assert not (settings.uploads_dir / f"{pid}.jpg").exists()
    assert cb.get("/api/couple/status").json()["matched"] is False
    _signup("mu_a8")                                           # 같은 아이디 재가입
    assert _cookie_client(tok).get("/api/auth/me").json()["authenticated"] is False


def test_decline_after_accept_is_rejected():
    ca, cb = _signup("mu_a9"), _signup("mu_b9")
    iid = ca.post("/api/couple/invite", json={"email": "mu_b9"}).json()["id"]
    cb.post(f"/api/couple/invites/{iid}/accept")
    assert cb.post(f"/api/couple/invites/{iid}/decline").status_code == 400
    assert ca.post(f"/api/couple/invites/{iid}/cancel").status_code == 400


def test_signup_rate_limited_per_ip():
    codes = [TestClient(app).post("/api/auth/signup", json={"username": f"mu_rl{i}", "password": PW}).status_code
             for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_photo_quota(monkeypatch):
    from app.routes import photos_routes
    ca, _ = _pair("mu_a10", "mu_b10")
    monkeypatch.setattr(photos_routes, "COUPLE_QUOTA", 10)
    buf = io.BytesIO(); Image.new("RGB", (8, 8)).save(buf, "JPEG")
    r = ca.post("/api/photos/upload", files={"file": ("a.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 413 and r.json()["detail"] == "quota_exceeded"


def test_allow_sliding_window():
    assert [auth.allow("t:x", 2, 60) for _ in range(3)] == [True, True, False]


def test_cross_site_write_blocked_same_site_allowed():
    c = _signup("mu_o1")
    bad = c.post("/api/couple/unlink", headers={"Origin": "https://evil.ai-ve.uk"})
    assert bad.status_code == 403 and bad.json()["detail"] == "bad_origin"
    ok = c.post("/api/auth/logout", headers={"Origin": "https://couple.ai-ve.uk"})
    assert ok.status_code == 200


def test_invite_rate_limited():
    ca = _signup("mu_i1")
    codes = {ca.post("/api/couple/invite", json={"email": f"nobody{i}"}).status_code for i in range(21)}
    assert 429 in codes


def test_bootstrap_does_not_steal_bound_members(monkeypatch):
    from app import db
    from app.config import settings
    _pair("mu_s1", "mu_s2")
    with cursor() as cur:
        seed = dict(cur.execute("SELECT * FROM couples WHERE id=1").fetchone())
        cur.execute("DELETE FROM couples WHERE id=1")
        cur.execute("INSERT INTO bucket (id, title, created_at) VALUES ('mu-null', 'x', 'x')")
    monkeypatch.setattr(settings, "allowed_emails", ["mu_s1", "mu_s2"])
    cid = auth.couple_of("mu_s1")
    try:
        db._bootstrap_couple_one()
        assert auth.couple_of("mu_s1") == cid
        with cursor() as cur:
            assert cur.execute("SELECT COUNT(*) n FROM couples WHERE id=1").fetchone()["n"] == 0
    finally:                                   # conftest 의 couple #1 시드 복구
        with cursor() as cur:
            cur.execute("DELETE FROM bucket WHERE id='mu-null'")
            cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                        "VALUES (?, ?, ?, ?)", (1, seed["member_a"], seed["member_b"], seed["created_at"]))

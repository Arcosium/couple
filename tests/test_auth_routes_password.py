from fastapi.testclient import TestClient
from server import app
from app.config import settings


def _client():
    return TestClient(app)            # 인스턴스마다 독립 쿠키 jar


def test_signup_then_me_authenticated():
    c = _client()
    r = c.post("/api/auth/signup", json={"username": "routeA", "password": "pw12345678"})
    assert r.status_code == 200 and r.json()["email"] == "routea"
    me = c.get("/api/auth/me")        # 쿠키 자동 동봉
    assert me.json()["authenticated"] is True


def test_login_wrong_password():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeB", "password": "pw12345678"})
    bad = c.post("/api/auth/login", json={"username": "routeB", "password": "nope!!!!"})
    assert bad.status_code == 401


def test_signup_duplicate_400():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeC", "password": "pw12345678"})
    dup = c.post("/api/auth/signup", json={"username": "routeC", "password": "pw12345678"})
    assert dup.status_code == 400 and dup.json()["detail"] == "username_taken"


def test_signup_closed_403(monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    c = _client()
    r = c.post("/api/auth/signup", json={"username": "routeD", "password": "pw12345678"})
    assert r.status_code == 403


def test_claim_preserves_legacy_couple():
    # conftest 가 couple #1 을 a@test/b@test 로 시드. a@test 는 username NULL → claim 가능.
    legacy = (settings.allowed_emails + ["a@test"])[0]
    c = _client()
    r = c.post("/api/auth/claim",
               json={"email": legacy, "username": "claimeduser", "password": "pw12345678"})
    assert r.status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["matched"] is True   # 커플 #1 보존


def test_logout_clears_session():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeE", "password": "pw12345678"})
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json()["authenticated"] is False

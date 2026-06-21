"""CF Access 헤더는 더 이상 신뢰하지 않는다(자체 비밀번호 인증으로 전환).
헤더만으로는 인증되지 않아야 하고, 세션 쿠키로만 인증된다.
"""
from fastapi.testclient import TestClient
from server import app

ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


def _client():
    return TestClient(app)


def test_access_header_no_longer_authenticates():
    c = _client()
    r = c.get("/api/auth/me", headers={ACCESS_HEADER: "stranger@example.com"})
    assert r.json()["authenticated"] is False        # 헤더 위조 차단(fail-closed)


def test_no_cookie_is_unauthenticated():
    assert _client().get("/api/auth/me").json()["authenticated"] is False


def test_cookie_session_authenticates():
    c = _client()
    c.post("/api/auth/signup", json={"username": "hdru1", "password": "pw12345678"})
    assert c.get("/api/auth/me").json()["authenticated"] is True


def test_index_serves_login_when_only_header_present():
    c = _client()
    anon = c.get("/", headers={ACCESS_HEADER: "stranger@example.com"}).text
    # 헤더만으로는 인증 안 됨 → 로그인 화면. (Task 8 시점 OTP 폼 / Task 9 이후 회원가입 폼)
    assert ("회원가입" in anon) or ("인증 코드 받기" in anon)

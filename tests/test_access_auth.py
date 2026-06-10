"""Cloudflare Access 헤더 신원을 신뢰해 자동 로그인하는지 검증.

couple 은 Cloudflare Access 뒤에 있고, Access 가 검증한 이메일을
`Cf-Access-Authenticated-User-Email` 헤더로 origin 에 전달한다.
앱은 자체 OTP 없이 이 헤더만으로 로그인 상태가 되어야 한다.
"""
from fastapi.testclient import TestClient

from server import app
from app.config import settings

client = TestClient(app)

ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"
ALLOWED = settings.allowed_emails[0]


def test_access_header_authenticates_me():
    r = client.get("/api/auth/me", headers={ACCESS_HEADER: ALLOWED})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["email"] == ALLOWED


def test_no_header_no_cookie_is_unauthenticated():
    r = client.get("/api/auth/me")
    assert r.json()["authenticated"] is False


def test_disallowed_access_email_rejected():
    r = client.get("/api/auth/me", headers={ACCESS_HEADER: "stranger@example.com"})
    assert r.json()["authenticated"] is False


def test_index_serves_app_not_login_for_access_user():
    # 인증되면 app.html, 아니면 login.html 이 렌더된다.
    authed = client.get("/", headers={ACCESS_HEADER: ALLOWED}).text
    anon = client.get("/").text
    assert authed != anon
    assert "인증 코드 받기" in anon          # login.html 마커
    assert "인증 코드 받기" not in authed     # app.html 에는 없어야

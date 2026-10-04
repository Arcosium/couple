from app import auth
from app.config import settings


def test_couple_of_and_partner(monkeypatch):
    # conftest 가 couple #1 을 allowed_emails[0],[1] 로 시드함
    a, b = settings.allowed_emails[0], settings.allowed_emails[1]
    assert auth.couple_of(a) == 1
    assert auth.partner_of(a) == b
    assert auth.couple_of("nobody@test") is None
    assert auth.partner_of("nobody@test") is None

from app.config import settings


def test_flags_default_true():
    assert settings.allow_signup is True
    assert settings.allow_legacy_claim is True

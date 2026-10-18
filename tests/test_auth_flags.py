from app.config import settings


def test_flag_defaults():
    assert settings.allow_signup is True
    assert settings.allow_legacy_claim is False     # 이관 끝 → 이어받기 기본 닫힘

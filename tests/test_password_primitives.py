import pytest
from app import auth


def test_hash_and_verify_roundtrip():
    h = auth.hash_password("correct horse")
    assert h != "correct horse"          # 평문 아님
    assert auth.verify_password(h, "correct horse") is True
    assert auth.verify_password(h, "wrong") is False


def test_verify_handles_garbage_hash():
    assert auth.verify_password("", "x") is False
    assert auth.verify_password("not-a-hash", "x") is False


def test_validate_username_ok_normalizes_lower():
    assert auth.validate_username("Alice_01") == "alice_01"


@pytest.mark.parametrize("bad", ["", "ab", "a" * 33, "has space", "한글", "no@at"])
def test_validate_username_rejects(bad):
    with pytest.raises(ValueError):
        auth.validate_username(bad)


def test_validate_password_min_length():
    auth.validate_password("12345678")          # ok, 8자
    with pytest.raises(ValueError):
        auth.validate_password("short")

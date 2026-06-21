import pytest
from app import auth
from app.db import cursor


def test_create_user_sets_email_equals_username():
    ident = auth.create_user("storeuser1", "pw12345678")
    assert ident == "storeuser1"
    with cursor() as cur:
        row = cur.execute("SELECT email, username, password_hash FROM users "
                          "WHERE username='storeuser1'").fetchone()
    assert row["email"] == "storeuser1"
    assert row["password_hash"]


def test_create_user_duplicate_rejected():
    auth.create_user("dupuser", "pw12345678")
    with pytest.raises(ValueError):
        auth.create_user("dupuser", "pw12345678")


def test_authenticate_good_and_bad():
    auth.create_user("loginuser", "pw12345678")
    assert auth.authenticate("loginuser", "pw12345678") == "loginuser"
    assert auth.authenticate("loginuser", "wrongpass") is None
    assert auth.authenticate("ghost", "whatever") is None


def test_claim_legacy_preserves_identity():
    # 레거시 행(username NULL) 시드
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email, couple_id) VALUES ('old@x.com', 1)")
    ident = auth.claim_legacy("old@x.com", "claimed01", "pw12345678")
    assert ident == "old@x.com"                       # identity 는 옛 email 유지
    assert auth.authenticate("claimed01", "pw12345678") == "old@x.com"
    with cursor() as cur:
        row = cur.execute("SELECT couple_id FROM users WHERE email='old@x.com'").fetchone()
    assert row["couple_id"] == 1                       # 옛 데이터 연결 보존


def test_claim_rejects_already_claimed_and_missing():
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('once@x.com')")
    auth.claim_legacy("once@x.com", "onceclaim", "pw12345678")
    with pytest.raises(ValueError):                    # 이미 claim 됨
        auth.claim_legacy("once@x.com", "another", "pw12345678")
    with pytest.raises(ValueError):                    # 존재하지 않는 email
        auth.claim_legacy("nobody@x.com", "x", "pw12345678")

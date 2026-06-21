from app import auth


def setup_function():
    auth._attempts.clear()


def test_locks_after_max_fails():
    for _ in range(auth.MAX_FAILS):
        assert auth.is_locked("victim") is False
        auth.record_failure("victim")
    assert auth.is_locked("victim") is True


def test_clear_resets():
    for _ in range(auth.MAX_FAILS):
        auth.record_failure("v2")
    auth.clear_attempts("v2")
    assert auth.is_locked("v2") is False


def test_window_expiry_unlocks(monkeypatch):
    import app.auth as a
    t = [1000.0]
    monkeypatch.setattr(a.time, "time", lambda: t[0])
    for _ in range(a.MAX_FAILS):
        a.record_failure("v3")
    assert a.is_locked("v3") is True
    t[0] += a.LOCK_SECONDS + 1            # 잠금창 경과
    assert a.is_locked("v3") is False

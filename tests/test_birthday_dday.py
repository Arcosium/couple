"""생일 D-Day(다음 생일까지 카운트다운) 계산 검증."""
from datetime import date

from app.routes.settings_routes import _next_birthday


def test_birthday_later_this_year():
    # 실제 개인정보와 무관한 가상 생일로 계산을 확인한다.
    r = _next_birthday("1990-10-09", today=date(2026, 5, 29))
    assert r["next"] == "2026-10-09"
    assert r["in_days"] == (date(2026, 10, 9) - date(2026, 5, 29)).days
    assert r["turning_age"] == 36
    assert r["birth_date"] == "1990-10-09"


def test_birthday_already_passed_this_year_rolls_to_next():
    # 실제 개인정보와 무관한 가상 생일로 계산을 확인한다.
    r = _next_birthday("1992-02-15", today=date(2026, 5, 29))
    assert r["next"] == "2027-02-15"
    assert r["in_days"] == (date(2027, 2, 15) - date(2026, 5, 29)).days
    assert r["turning_age"] == 35


def test_birthday_today_is_d0():
    r = _next_birthday("2000-05-29", today=date(2026, 5, 29))
    assert r["in_days"] == 0
    assert r["next"] == "2026-05-29"
    assert r["turning_age"] == 26


def test_empty_or_invalid_returns_none():
    assert _next_birthday("", today=date(2026, 5, 29)) is None
    assert _next_birthday(None, today=date(2026, 5, 29)) is None
    assert _next_birthday("not-a-date", today=date(2026, 5, 29)) is None


def test_dday_endpoint_includes_both_birthdays():
    from fastapi.testclient import TestClient
    from server import app
    from app.config import settings
    from app.auth import SESSION_COOKIE, make_session_cookie

    r = TestClient(app).get(
        "/api/settings/dday",
        headers={"Cookie": f"{SESSION_COOKIE}={make_session_cookie(settings.allowed_emails[0])}"},
    )
    body = r.json()
    assert "birthdays" in body
    by_who = {b["who"]: b for b in body["birthdays"]}
    assert set(by_who) == {"a", "b"}
    assert by_who["a"]["nickname"] and by_who["b"]["nickname"]
    assert all(0 <= b["in_days"] <= 366 for b in body["birthdays"])

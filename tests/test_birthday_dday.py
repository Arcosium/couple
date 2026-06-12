"""생일 D-Day(다음 생일까지 카운트다운) 계산 검증."""
from datetime import date

from app.routes.settings_routes import _next_birthday


def test_birthday_later_this_year():
    # 현호 9/8 생일, 기준 5/29 → 올해 생일 아직 안 옴
    r = _next_birthday("1999-09-08", today=date(2026, 5, 29))
    assert r["next"] == "2026-09-08"
    assert r["in_days"] == (date(2026, 9, 8) - date(2026, 5, 29)).days
    assert r["turning_age"] == 27
    assert r["birth_date"] == "1999-09-08"


def test_birthday_already_passed_this_year_rolls_to_next():
    # 숙영 1/30 생일, 기준 5/29 → 올해 생일 지남 → 내년으로
    r = _next_birthday("2001-01-30", today=date(2026, 5, 29))
    assert r["next"] == "2027-01-30"
    assert r["in_days"] == (date(2027, 1, 30) - date(2026, 5, 29)).days
    assert r["turning_age"] == 26


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

    r = TestClient(app).get(
        "/api/settings/dday",
        headers={"Cf-Access-Authenticated-User-Email": settings.allowed_emails[0]},
    )
    body = r.json()
    assert "birthdays" in body
    by_who = {b["who"]: b for b in body["birthdays"]}
    assert set(by_who) == {"a", "b"}
    assert by_who["a"]["nickname"] and by_who["b"]["nickname"]
    assert all(0 <= b["in_days"] <= 366 for b in body["birthdays"])

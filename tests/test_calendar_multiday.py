"""다중일(종료일) 일정 — 생성/조회/검증.

events 에 end_date(YYYY-MM-DD, 선택) 를 더해 2일 이상 이어지는 일정을 표현한다.
미입력이면 하루짜리(end_date=NULL).
"""
from fastapi.testclient import TestClient

from server import app
from app.config import settings

client = TestClient(app)
H = {"Cf-Access-Authenticated-User-Email": settings.allowed_emails[0]}


def _create(**kw):
    return client.post("/api/events", json=kw, headers=H)


def _get(eid):
    rows = client.get("/api/events", headers=H).json()
    return next((r for r in rows if r["id"] == eid), None)


def _delete(eid):
    client.delete(f"/api/events/{eid}", headers=H)


def test_event_with_end_date_roundtrips():
    eid = _create(title="여행", due="2026-07-01", end_date="2026-07-03").json()["id"]
    try:
        row = _get(eid)
        assert row["due"] == "2026-07-01"
        assert row["end_date"] == "2026-07-03"
    finally:
        _delete(eid)


def test_event_without_end_date_is_null():
    eid = _create(title="하루", due="2026-07-10").json()["id"]
    try:
        assert _get(eid)["end_date"] is None
    finally:
        _delete(eid)


def test_end_date_before_due_is_dropped():
    # 종료일이 시작일보다 앞서면 하루짜리로 취급(end_date=NULL).
    eid = _create(title="거꾸로", due="2026-07-10", end_date="2026-07-05").json()["id"]
    try:
        assert _get(eid)["end_date"] is None
    finally:
        _delete(eid)


def test_same_day_end_date_is_dropped():
    # 종료일==시작일은 굳이 저장하지 않는다(하루짜리와 동일).
    eid = _create(title="같은날", due="2026-07-10", end_date="2026-07-10").json()["id"]
    try:
        assert _get(eid)["end_date"] is None
    finally:
        _delete(eid)


def test_bad_end_date_rejected():
    r = _create(title="나쁨", due="2026-07-10", end_date="not-a-date")
    assert r.status_code == 400


def test_patch_end_date():
    eid = _create(title="연장", due="2026-08-01").json()["id"]
    try:
        client.patch(f"/api/events/{eid}", json={"end_date": "2026-08-04"}, headers=H)
        assert _get(eid)["end_date"] == "2026-08-04"
    finally:
        _delete(eid)

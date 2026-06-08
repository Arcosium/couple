"""기념일·생일·일수 마일스톤(100일·200일…)을 캘린더에 자동 기록.

- **멱등(idempotent)**: 결정적 id 를 써서 여러 번 호출해도 중복이 안 생긴다.
- **다가오는 것만**: 오늘 이후 ~ horizon(약 2년) 안에 드는 특별일만 만든다.
- **source='auto'**: 사용자가 직접 만든 일정과 구분(달력에서 지난 건 done 처리됨).
- 기념일/생일 날짜가 바뀌면 `clear_auto(prefix)` 로 옛 자동 일정을 지우고 다시 생성.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .config import settings as cfg
from .db import cursor, kv_get

# D-day 칩과 동일한 마일스톤 집합(일수 = days_together; 100일 = 만난 날 + 99일)
MILESTONES = [22, 50, 100, 200, 300, 365, 500, 700, 1000, 1500, 2000, 3000]
_HORIZON_DAYS = 800   # 너무 먼 미래까지 미리 채우지 않도록 약 2년 앞까지만


def _parse(d: str | None) -> date | None:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _yearly(base: date, year: int) -> date:
    """base 의 월/일을 주어진 연도로. 2/29 는 평년이면 2/28 로."""
    try:
        return base.replace(year=year)
    except ValueError:
        return base.replace(year=year, month=2, day=28)


def _upsert(eid: str, title: str, due: date, color: str) -> int:
    """없으면 INSERT(1), 이미 있으면 그대로(0). 멱등 보장의 핵심."""
    with cursor() as cur:
        if cur.execute("SELECT 1 FROM events WHERE id=?", (eid,)).fetchone():
            return 0
        cur.execute(
            "INSERT INTO events (id, title, due, time, note, color, source, done, "
            "reminder_minutes, owner_email, created_at) "
            "VALUES (?, ?, ?, NULL, NULL, ?, 'auto', 0, NULL, '', ?)",
            (eid, title, due.isoformat(), color,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    return 1


def clear_auto(prefix: str) -> None:
    """특정 종류의 자동 일정을 모두 삭제(예: 'auto-mile-', 'auto-bday-a-').
    기념일/생일 날짜가 바뀌어 옛 자동 일정이 어긋났을 때 재생성 전에 호출."""
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE source='auto' AND id LIKE ?", (prefix + "%",))


def ensure_special_events(today: date | None = None) -> int:
    """다가오는 기념일·생일·마일스톤 캘린더 일정을 보장. 생성한 개수 반환."""
    today = today or date.today()
    horizon = today + timedelta(days=_HORIZON_DAYS)
    created = 0

    start = _parse(kv_get("anniversary_date", cfg.anniversary_date))
    if start:
        # 일수 마일스톤 (100일 = days_together 100 = start + 99일)
        for m in MILESTONES:
            d = start + timedelta(days=m - 1)
            if today <= d <= horizon:
                created += _upsert(f"auto-mile-{m}", f"💞 {m}일", d, "#ec4899")
        # 연 기념일 (1주년·2주년 …) — 앞으로 5년
        for yr in range(today.year, today.year + 6):
            d = _yearly(start, yr)
            n = yr - start.year
            if n >= 1 and today <= d <= horizon:
                created += _upsert(f"auto-anniv-{yr}", f"💍 {n}주년", d, "#f43f5e")

    # 각자 생일 — 앞으로 3년
    for who, bkey, bdef, nkey, ndef in (
        ("a", "birthday_a", cfg.birthday_a, "nickname_a", cfg.nickname_a),
        ("b", "birthday_b", cfg.birthday_b, "nickname_b", cfg.nickname_b),
    ):
        born = _parse(kv_get(bkey, bdef))
        if not born:
            continue
        nick = kv_get(nkey, ndef)
        for yr in range(today.year, today.year + 4):
            d = _yearly(born, yr)
            if today <= d <= horizon:
                created += _upsert(f"auto-bday-{who}-{yr}", f"🎂 {nick} 생일", d, "#f59e0b")

    return created

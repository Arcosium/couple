from datetime import date, datetime
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import require_couple
from ..config import settings as cfg
from ..db import kv_get, kv_set

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPatch(BaseModel):
    anniversary_date: str | None = None
    nickname_a: str | None = None
    nickname_b: str | None = None
    birthday_a: str | None = None
    birthday_b: str | None = None
    theme: str | None = None
    mascot: str | None = None


def _current(cid: int) -> dict:
    return {
        "anniversary_date": kv_get(cid, "anniversary_date", cfg.anniversary_date),
        "nickname_a": kv_get(cid, "nickname_a", cfg.nickname_a),
        "nickname_b": kv_get(cid, "nickname_b", cfg.nickname_b),
        "birthday_a": kv_get(cid, "birthday_a", cfg.birthday_a),
        "birthday_b": kv_get(cid, "birthday_b", cfg.birthday_b),
        "theme": kv_get(cid, "theme", "rosy"),
        "mascot": kv_get(cid, "mascot", "bunny"),
        "kakao_js_key": cfg.kakao_js_key,
    }


def _next_birthday(bday: str | None, today: date | None = None) -> dict | None:
    """생일 문자열(YYYY-MM-DD)로 '다음 생일' 정보를 계산. 비거나 형식 오류면 None."""
    if not bday:
        return None
    try:
        born = datetime.strptime(bday, "%Y-%m-%d").date()
    except ValueError:
        return None
    today = today or date.today()

    def _on(year: int) -> date:
        try:
            return born.replace(year=year)
        except ValueError:  # 2/29 → 평년엔 2/28 로
            return born.replace(year=year, month=2, day=28)

    nxt = _on(today.year)
    if nxt < today:
        nxt = _on(today.year + 1)
    return {
        "birth_date": born.isoformat(),
        "next": nxt.isoformat(),
        "in_days": (nxt - today).days,
        "turning_age": nxt.year - born.year,
    }


@router.get("")
def get_settings(request: Request):
    _email, cid = require_couple(request)
    return _current(cid)


@router.patch("")
def patch_settings(body: SettingsPatch, request: Request):
    _email, cid = require_couple(request)
    # 자동 캘린더 일정(기념일·생일·마일스톤) 재생성이 필요한 변경 추적
    stale: set[str] = set()
    if body.anniversary_date:
        try:
            datetime.strptime(body.anniversary_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_date")
        kv_set(cid, "anniversary_date", body.anniversary_date)
        stale |= {"auto-mile-", "auto-anniv-"}     # 만난 날 바뀌면 일수·연 기념일 어긋남
    if body.nickname_a is not None:
        kv_set(cid, "nickname_a", body.nickname_a.strip() or cfg.nickname_a)
        stale.add("auto-bday-a-")                   # 생일 일정 제목에 닉네임이 들어감
    if body.nickname_b is not None:
        kv_set(cid, "nickname_b", body.nickname_b.strip() or cfg.nickname_b)
        stale.add("auto-bday-b-")
    for fld, val, prefix in (("birthday_a", body.birthday_a, "auto-bday-a-"),
                             ("birthday_b", body.birthday_b, "auto-bday-b-")):
        if val is not None:
            if val and _next_birthday(val) is None:
                raise HTTPException(status_code=400, detail="bad_date")
            kv_set(cid, fld, val)
            stale.add(prefix)
    if body.theme:
        kv_set(cid, "theme", body.theme)
    if body.mascot:
        kv_set(cid, "mascot", body.mascot)
    if stale:
        from ..special_events import clear_auto, ensure_special_events
        for prefix in stale:
            clear_auto(cid, prefix)
        ensure_special_events(cid)
    return _current(cid)


@router.get("/dday")
def dday(request: Request):
    """커플 D-day."""
    _email, cid = require_couple(request)
    anniv = kv_get(cid, "anniversary_date", cfg.anniversary_date)
    try:
        start = datetime.strptime(anniv, "%Y-%m-%d").date()
    except Exception:
        start = date.today()
    today = date.today()
    days = (today - start).days
    dt = days + 1                # days_together (만난 날=1일) — 마일스톤은 이 단위로 센다
    # 100일/1000일 등 다음 마일스톤 (100일 = 만난 날로부터 99일째 날, days_together==100)
    milestones = [22, 50, 100, 200, 300, 365, 500, 700, 1000, 1500, 2000, 3000]
    next_m = next((m for m in milestones if m >= dt), ((dt // 100) + 1) * 100)
    # 다음 연 기념일
    try:
        anniv_this_year = start.replace(year=today.year)
    except ValueError:
        anniv_this_year = start
    if anniv_this_year < today:
        try:
            anniv_next = start.replace(year=today.year + 1)
        except ValueError:
            anniv_next = anniv_this_year
    else:
        anniv_next = anniv_this_year
    # 각자 생일 D-day (다음 생일까지)
    birthdays = []
    for who, bkey, bdefault, nkey, ndefault in (
        ("a", "birthday_a", cfg.birthday_a, "nickname_a", cfg.nickname_a),
        ("b", "birthday_b", cfg.birthday_b, "nickname_b", cfg.nickname_b),
    ):
        info = _next_birthday(kv_get(cid, bkey, bdefault), today)
        if info:
            info["who"] = who
            info["nickname"] = kv_get(cid, nkey, ndefault)
            birthdays.append(info)
    return {
        "anniversary_date": anniv,
        "days_together": days + 1,  # 만난 날=1일
        "since": start.isoformat(),
        "next_milestone_days": next_m,
        "next_milestone_in": next_m - dt,
        "next_anniversary": anniv_next.isoformat(),
        "next_anniversary_in": (anniv_next - today).days,
        "birthdays": birthdays,
    }

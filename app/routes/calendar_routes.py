import secrets
import time as _time
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import require_user, partner_of
from ..db import cursor
from ..realtime import hub

router = APIRouter(prefix="/api/events", tags=["events"])


class EventIn(BaseModel):
    title: str
    due: str | None = None
    end_date: str | None = None        # 2일 이상 이어지는 일정의 종료일(선택). 미입력=하루
    time: str | None = None
    note: str | None = None
    color: str | None = None
    source: str = "calendar"           # 'calendar' | 'todo'
    reminder_minutes: int | None = None


class EventPatch(BaseModel):
    title: str | None = None
    due: str | None = None
    end_date: str | None = None
    time: str | None = None
    note: str | None = None
    color: str | None = None
    done: bool | None = None
    reminder_minutes: int | None = None


def _norm_end_date(end_date: str | None, due: str | None) -> str | None:
    """종료일 정규화: 형식 검증 후, 시작일 이하면 NULL(=하루짜리)로 떨군다.

    잘못된 형식은 400. ISO 날짜는 사전식 비교가 곧 날짜 비교이므로 문자열 비교로 충분.
    """
    if not end_date:
        return None
    try:
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="bad_end_date")
    if due and end_date <= due:
        return None
    return end_date


def _auto_complete_past(rows: list[dict]) -> bool:
    """캘린더 일정 중 시각이 지난 건 자동으로 done 처리."""
    now = datetime.now()
    changed = False
    for r in rows:
        if r.get("source") not in ("calendar", "auto") or r.get("done"):
            continue
        due = r.get("due")
        if not due:
            continue
        tm = r.get("time") or "23:59"
        try:
            ev = datetime.strptime(f"{due} {tm}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        if now > ev:
            with cursor() as cur:
                cur.execute("UPDATE events SET done=1 WHERE id=?", (r["id"],))
            r["done"] = 1
            changed = True
    return changed


@router.get("")
def list_events(request: Request):
    require_user(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM events ORDER BY due IS NULL, due ASC, time IS NULL, time ASC, created_at DESC"
        ).fetchall()]
    _auto_complete_past(rows)
    return rows


@router.post("")
async def create(body: EventIn, request: Request):
    user = require_user(request)
    eid = str(int(_time.time() * 1000))
    if body.due:
        try:
            datetime.strptime(body.due, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_due")
    end_date = _norm_end_date(body.end_date, body.due)
    if body.time:
        try:
            datetime.strptime(body.time, "%H:%M")
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_time")
    with cursor() as cur:
        cur.execute(
            """INSERT INTO events
               (id, title, due, end_date, time, note, color, source, done, reminder_minutes,
                owner_email, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (
                eid, body.title.strip(), body.due, end_date, body.time, body.note,
                body.color, body.source, body.reminder_minutes, user,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
        )
    payload = {"kind": "event_added", "title": body.title, "by": user, "due": body.due}
    if (p := partner_of(user)):
        await hub.send(p, payload)
    return {"ok": True, "id": eid}


@router.patch("/{eid}")
async def patch(eid: str, body: EventPatch, request: Request):
    user = require_user(request)
    fields = []
    args: list = []
    for key in ("title", "due", "time", "note", "color", "reminder_minutes"):
        val = getattr(body, key)
        if val is not None:
            fields.append(f"{key}=?")
            args.append(val)
    # end_date: 빈 문자열("")=지움(NULL), 날짜=설정, None(미전송)=변경 없음.
    if body.end_date is not None:
        fields.append("end_date=?")
        args.append(_norm_end_date(body.end_date or None, body.due))
    if body.done is not None:
        fields.append("done=?")
        args.append(1 if body.done else 0)
    if not fields:
        return {"ok": True}
    args.append(eid)
    with cursor() as cur:
        cur.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=?", args)
    if (p := partner_of(user)) and body.done:
        await hub.send(p, {"kind": "event_done", "by": user})
    return {"ok": True}


@router.delete("/{eid}")
def delete(eid: str, request: Request):
    require_user(request)
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE id=?", (eid,))
    return {"ok": True}

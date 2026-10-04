import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import require_couple, partner_of
from ..db import cursor
from ..realtime import hub

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteIn(BaseModel):
    date: str
    content: str = ""


def _valid_date(d: str) -> bool:
    try:
        datetime.strptime(d, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


@router.get("/all")
def all_notes(request: Request):
    """커플의 모든 오늘 한마디 — 캘린더 셀에 날짜별로 표시하기 위함.

    /api/events 와 동일하게 커플 전체를 한 번에 내려준다(2인 앱이라 양 적음).
    각 행에 요청자 기준 mine 플래그를 붙여 프런트가 내/상대를 구분한다.
    """
    email, cid = require_couple(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT author_email, date, content, updated_at FROM daily_notes "
            "WHERE couple_id=? ORDER BY date ASC", (cid,)).fetchall()]
    for r in rows:
        r["mine"] = (r["author_email"] == email)
    return rows


@router.get("")
def get_notes(request: Request, date: str):
    email, cid = require_couple(request)
    if not _valid_date(date):
        raise HTTPException(status_code=400, detail="bad_date")
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT author_email, content, updated_at FROM daily_notes "
            "WHERE couple_id=? AND date=?", (cid, date)).fetchall()]
    mine = next((r for r in rows if r["author_email"] == email), None)
    partner = next((r for r in rows if r["author_email"] != email), None)
    return {"date": date, "mine": mine, "partner": partner}


@router.put("")
async def put_note(body: NoteIn, request: Request):
    email, cid = require_couple(request)
    if not _valid_date(body.date):
        raise HTTPException(status_code=400, detail="bad_date")
    content = (body.content or "").strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with cursor() as cur:
        if not content:
            cur.execute("DELETE FROM daily_notes WHERE couple_id=? AND author_email=? AND date=?",
                        (cid, email, body.date))
            return {"ok": True, "deleted": True}
        cur.execute(
            "INSERT INTO daily_notes (id, couple_id, author_email, date, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(couple_id, author_email, date) DO UPDATE SET "
            "content=excluded.content, updated_at=excluded.updated_at",
            (secrets.token_urlsafe(8), cid, email, body.date, content, now, now))
    p = partner_of(email)
    if p:
        await hub.send(p, {"kind": "note", "date": body.date, "from": email})
    return {"ok": True}

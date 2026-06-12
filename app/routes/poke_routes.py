import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import partner_of, require_couple
from ..db import cursor
from ..realtime import hub

router = APIRouter(prefix="/api/pokes", tags=["pokes"])


# 미리 정의된 콕찌르기 종류 — 둘만의 작은 사전
PRESETS = [
    {"emoji": "👉", "label": "콕!"},
    {"emoji": "🥺", "label": "보고싶어"},
    {"emoji": "💗", "label": "사랑해"},
    {"emoji": "🫶", "label": "사랑해♥"},
    {"emoji": "😘", "label": "뽀뽀"},
    {"emoji": "🤗", "label": "안아줘"},
    {"emoji": "😡", "label": "삐졌어"},
    {"emoji": "🍽️", "label": "밥 먹자"},
    {"emoji": "😴", "label": "잘자"},
    {"emoji": "📞", "label": "전화해"},
]


class PokeIn(BaseModel):
    emoji: str
    message: str | None = None


@router.get("/presets")
def presets():
    return PRESETS


@router.get("")
def list_pokes(request: Request):
    user, cid = require_couple(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM pokes WHERE (to_email=? OR from_email=?) AND couple_id=? "
            "ORDER BY created_at DESC LIMIT 80",
            (user, user, cid),
        ).fetchall()]
    return rows


@router.get("/unread_count")
def unread_count(request: Request):
    user, cid = require_couple(request)
    with cursor() as cur:
        row = cur.execute(
            "SELECT COUNT(*) AS n FROM pokes WHERE to_email=? AND seen=0 AND couple_id=?",
            (user, cid),
        ).fetchone()
    return {"unread": row["n"]}


@router.post("")
async def send(body: PokeIn, request: Request):
    user, cid = require_couple(request)
    partner = partner_of(user)
    if not partner:
        raise HTTPException(status_code=400, detail="no_partner_configured")
    pid = secrets.token_urlsafe(8)
    # tz-aware UTC(+00:00 포함)로 저장/전송 — 서버(UTC) datetime.now() 를 무-tz 로 넘기면
    # 브라우저가 로컬(KST)로 파싱해 9시간 어긋난다. offset 을 박아 정확히 표시되게 한다.
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO pokes (id, from_email, to_email, emoji, message, seen, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (pid, user, partner, body.emoji, body.message, created, cid),
        )
    sent = await hub.send(
        partner,
        {
            "kind": "poke",
            "id": pid,
            "emoji": body.emoji,
            "message": body.message or "",
            "from": user,
            "created_at": created,
        },
    )
    return {"ok": True, "id": pid, "delivered_live": sent}


@router.post("/seen")
def mark_seen(request: Request):
    user, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("UPDATE pokes SET seen=1 WHERE to_email=? AND seen=0 AND couple_id=?",
                    (user, cid))
    return {"ok": True}


@router.post("/clear")
def clear(request: Request):
    """내가 보냈거나 받은 콕찌르기 기록을 전부 삭제."""
    user, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM pokes WHERE (to_email=? OR from_email=?) AND couple_id=?",
                    (user, user, cid))
    return {"ok": True}

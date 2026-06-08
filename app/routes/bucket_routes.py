import time as _time
from datetime import datetime
from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..auth import require_user, partner_of
from ..db import cursor
from ..realtime import hub

router = APIRouter(prefix="/api/bucket", tags=["bucket"])


class BucketIn(BaseModel):
    title: str
    description: str | None = None
    icon: str | None = None
    target_date: str | None = None
    priority: int = 0


class BucketPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    icon: str | None = None
    target_date: str | None = None
    priority: int | None = None
    done: bool | None = None


@router.get("")
def list_items(request: Request):
    require_user(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM bucket ORDER BY done ASC, priority DESC, created_at DESC"
        ).fetchall()]
    return rows


@router.post("")
async def create(body: BucketIn, request: Request):
    user = require_user(request)
    bid = str(int(_time.time() * 1000))
    with cursor() as cur:
        cur.execute(
            """INSERT INTO bucket
               (id, title, description, icon, target_date, priority, done,
                created_at, owner_email)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (
                bid, body.title.strip(), body.description,
                body.icon or "💖", body.target_date, body.priority,
                datetime.now().isoformat(timespec="seconds"), user,
            ),
        )
    if (p := partner_of(user)):
        await hub.send(p, {"kind": "bucket_added", "title": body.title, "by": user})
    return {"ok": True, "id": bid}


@router.patch("/{bid}")
async def patch(bid: str, body: BucketPatch, request: Request):
    user = require_user(request)
    fields = []
    args: list = []
    for key in ("title", "description", "icon", "target_date", "priority"):
        v = getattr(body, key)
        if v is not None:
            fields.append(f"{key}=?")
            args.append(v)
    if body.done is not None:
        fields.append("done=?")
        args.append(1 if body.done else 0)
        if body.done:
            fields.append("done_at=?")
            args.append(datetime.now().isoformat(timespec="seconds"))
    if not fields:
        return {"ok": True}
    args.append(bid)
    with cursor() as cur:
        cur.execute(f"UPDATE bucket SET {', '.join(fields)} WHERE id=?", args)
    if body.done and (p := partner_of(user)):
        await hub.send(p, {"kind": "bucket_done", "by": user})
    return {"ok": True}


@router.delete("/{bid}")
def delete(bid: str, request: Request):
    require_user(request)
    with cursor() as cur:
        cur.execute("DELETE FROM bucket WHERE id=?", (bid,))
    return {"ok": True}

import time as _time
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from ..auth import require_couple, partner_of
from ..db import cursor
from ..realtime import hub

router = APIRouter(prefix="/api/places", tags=["places"])


class PlaceIn(BaseModel):
    name: str
    address: str | None = None
    lat: float
    lng: float
    kind: str = "wishlist"        # 'visited' | 'wishlist'
    category: str | None = None
    rating: int | None = None
    memo: str | None = None
    visited_at: str | None = None


class PlacePatch(BaseModel):
    name: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    kind: str | None = None
    category: str | None = None
    rating: int | None = None
    memo: str | None = None
    visited_at: str | None = None


@router.get("")
def list_places(request: Request, kind: str | None = None):
    _email, cid = require_couple(request)
    sql = "SELECT * FROM places WHERE couple_id=?"
    args: list = [cid]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY created_at DESC"
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql, args).fetchall()]
    return rows


@router.post("")
async def create(body: PlaceIn, request: Request):
    user, cid = require_couple(request)
    if body.kind not in ("visited", "wishlist", "revisit"):
        raise HTTPException(status_code=400, detail="bad_kind")
    pid = str(int(_time.time() * 1000))
    with cursor() as cur:
        cur.execute(
            """INSERT INTO places
               (id, name, address, lat, lng, kind, category, rating, memo,
                visited_at, created_at, owner_email, couple_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid, body.name.strip(), body.address, body.lat, body.lng,
                body.kind, body.category, body.rating, body.memo,
                body.visited_at, datetime.now().isoformat(timespec="seconds"), user, cid,
            ),
        )
    if (p := partner_of(user)):
        await hub.send(p, {
            "kind": "place_added",
            "name": body.name,
            "place_kind": body.kind,
            "by": user,
        })
    return {"ok": True, "id": pid}


@router.patch("/{pid}")
def patch(pid: str, body: PlacePatch, request: Request):
    _email, cid = require_couple(request)
    fields = []
    args: list = []
    for key in ("name", "address", "lat", "lng", "kind", "category", "rating", "memo", "visited_at"):
        v = getattr(body, key)
        if v is not None:
            fields.append(f"{key}=?")
            args.append(v)
    if not fields:
        return {"ok": True}
    args.append(pid)
    args.append(cid)
    with cursor() as cur:
        cur.execute(f"UPDATE places SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    return {"ok": True}


@router.delete("/{pid}")
def delete(pid: str, request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM places WHERE id=? AND couple_id=?", (pid, cid))
    return {"ok": True}

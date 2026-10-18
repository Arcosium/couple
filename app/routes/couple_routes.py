from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from .. import couples
from ..auth import allow, require_user, couple_of, partner_of
from ..db import kv_get, couple_members
from ..realtime import hub

router = APIRouter(prefix="/api/couple", tags=["couple"])


class InviteIn(BaseModel):
    # 상대 아이디(옛 계정은 이메일도 가능). 필드명은 기존 클라이언트 호환을 위해 email 유지.
    email: str


def _guard(fn):
    try:
        return fn()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status")
def status(request: Request):
    email = require_user(request)
    cid = couple_of(email)
    if not cid:
        return {"matched": False}
    partner = partner_of(email)
    members = couple_members(cid)
    is_a = bool(members) and email == members[0]
    return {
        "matched": True,
        "couple_id": cid,
        "partner_email": partner,
        "partner_nickname": kv_get(cid, "nickname_b" if is_a else "nickname_a", "") if partner else "",
    }


@router.post("/invite")
async def invite(body: InviteIn, request: Request):
    email = require_user(request)
    if not allow(f"invite:{email}", 20, 3600):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    inv = _guard(lambda: couples.create_invite(email, body.email))
    await hub.send(inv["invitee_email"], {"kind": "couple_invite", "from": email})
    return inv


@router.get("/invites")
def invites(request: Request):
    email = require_user(request)
    return couples.list_invites(email)


@router.post("/invites/{iid}/accept")
async def accept(iid: str, request: Request):
    email = require_user(request)
    res = _guard(lambda: couples.accept_invite(email, iid))
    await hub.send(res["partner"], {"kind": "couple_matched", "with": email})
    return {"ok": True, **res}


@router.post("/invites/{iid}/decline")
def decline(iid: str, request: Request):
    email = require_user(request)
    _guard(lambda: couples.decline_invite(email, iid))
    return {"ok": True}


@router.post("/invites/{iid}/cancel")
def cancel(iid: str, request: Request):
    email = require_user(request)
    _guard(lambda: couples.cancel_invite(email, iid))
    return {"ok": True}


@router.post("/unlink")
async def unlink(request: Request):
    email = require_user(request)
    cid = couple_of(email)
    if not cid:
        raise HTTPException(status_code=409, detail="no_couple")
    members = couples.unlink(cid)
    for em in members:
        if em != email:
            await hub.send(em, {"kind": "couple_unlinked", "by": email})
    return {"ok": True}

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel, EmailStr

from ..auth import (
    SESSION_COOKIE,
    consume_code,
    deliver_code,
    is_allowed,
    issue_code,
    make_session_cookie,
    read_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class EmailIn(BaseModel):
    email: EmailStr


class VerifyIn(BaseModel):
    email: EmailStr
    code: str


@router.post("/request_code")
def request_code(body: EmailIn):
    email = body.email.lower().strip()
    if not is_allowed(email):
        # 화이트리스트가 아니면 어떤 정보도 노출하지 않는다.
        raise HTTPException(status_code=403, detail="not_allowed")
    code = issue_code(email)
    res = deliver_code(email, code)
    return {"ok": True, "channel": res["channel"]}


@router.post("/verify")
def verify(body: VerifyIn, response: Response):
    email = body.email.lower().strip()
    code = body.code.strip()
    if not is_allowed(email):
        raise HTTPException(status_code=403, detail="not_allowed")
    if not consume_code(email, code):
        raise HTTPException(status_code=400, detail="invalid_or_expired")
    token = make_session_cookie(email)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=60 * 60 * 24 * 90,
        httponly=True,
        samesite="lax",
        secure=False,  # cloudflared가 TLS 종단, 내부는 http
    )
    return {"ok": True, "email": email}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    email = read_session(request)
    if not email:
        return {"authenticated": False}
    from ..auth import couple_of, partner_of
    from ..db import kv_get, couple_members
    cid = couple_of(email)
    if not cid:
        return {"authenticated": True, "email": email, "matched": False, "partner": None}
    members = couple_members(cid)
    is_a = bool(members) and email == members[0]
    my_bday_key = "birthday_a" if is_a else "birthday_b"
    # 온보딩 판정은 env 기본값을 무시한 raw kv(미설정=None)로만 — 신규 커플은 자기 값이 없음.
    needs_onboarding = (kv_get(cid, "anniversary_date", None) is None
                        or kv_get(cid, my_bday_key, None) is None)
    return {
        "authenticated": True,
        "email": email,
        "matched": True,
        "partner": partner_of(email),
        "nickname_self": kv_get(cid, "nickname_a" if is_a else "nickname_b", None),
        "nickname_partner": kv_get(cid, "nickname_b" if is_a else "nickname_a", None),
        "is_member_a": is_a,
        "needs_onboarding": needs_onboarding,
        "anniversary_raw": kv_get(cid, "anniversary_date", "") or "",
        "my_birthday_raw": kv_get(cid, my_bday_key, "") or "",
    }

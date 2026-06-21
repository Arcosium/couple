from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

from ..auth import (
    SESSION_COOKIE,
    authenticate,
    claim_legacy,
    clear_attempts,
    create_user,
    is_locked,
    make_session_cookie,
    read_session,
    record_failure,
)
from ..config import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


class ClaimIn(BaseModel):
    email: str
    username: str
    password: str


def _set_session(response: Response, email: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(email),
        max_age=60 * 60 * 24 * 90,
        httponly=True,
        samesite="lax",
        secure=False,  # cloudflared가 TLS 종단, 내부는 http
    )


@router.post("/signup")
def signup(body: Credentials, response: Response):
    if not settings.allow_signup:
        raise HTTPException(status_code=403, detail="signup_closed")
    try:
        email = create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _set_session(response, email)
    return {"ok": True, "email": email}


@router.post("/login")
def login(body: Credentials, response: Response):
    username = (body.username or "").strip().lower()
    if is_locked(username):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    email = authenticate(username, body.password)
    if not email:
        record_failure(username)
        raise HTTPException(status_code=401, detail="invalid_credentials")
    clear_attempts(username)
    _set_session(response, email)
    return {"ok": True, "email": email}


@router.post("/claim")
def claim(body: ClaimIn, response: Response):
    if not settings.allow_legacy_claim:
        raise HTTPException(status_code=403, detail="claim_closed")
    try:
        email = claim_legacy(body.email, body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _set_session(response, email)
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

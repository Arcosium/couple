from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

from ..auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    allow,
    authenticate,
    change_password,
    check_password,
    claim_legacy,
    clear_attempts,
    client_ip,
    create_user,
    is_locked,
    make_session_cookie,
    read_session,
    record_failure,
    require_user,
    rotate_session_key,
)
from ..config import settings
from ..realtime import hub

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


class ClaimIn(BaseModel):
    email: str
    username: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class PasswordIn(BaseModel):
    password: str


def _set_session(request: Request, response: Response, email: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session_cookie(email),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        # cloudflared 가 TLS 종단 + X-Forwarded-Proto(--proxy-headers) → 외부 https 면 Secure.
        secure=request.url.scheme == "https",
    )


@router.post("/signup")
def signup(body: Credentials, request: Request, response: Response):
    if not settings.allow_signup:
        raise HTTPException(status_code=403, detail="signup_closed")
    if not allow(f"signup:{client_ip(request)}", 5, 3600):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    try:
        email = create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _set_session(request, response, email)
    return {"ok": True, "email": email}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    username = (body.username or "").strip().lower()
    # 아이디별 잠금 + IP별 시도 상한(아이디를 바꿔 가며 대입하는 것 차단)
    if is_locked(username) or not allow(f"login:{client_ip(request)}", 30, 600):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    email = authenticate(username, body.password)
    if not email:
        record_failure(username)
        raise HTTPException(status_code=401, detail="invalid_credentials")
    clear_attempts(username)
    _set_session(request, response, email)
    return {"ok": True, "email": email}


@router.post("/claim")
def claim(body: ClaimIn, request: Request, response: Response):
    if not settings.allow_legacy_claim:
        raise HTTPException(status_code=403, detail="claim_closed")
    try:
        email = claim_legacy(body.email, body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _set_session(request, response, email)
    return {"ok": True, "email": email}


@router.post("/logout")
async def logout(request: Request, response: Response):
    # 쿠키만 지우면 복사된 쿠키가 계속 통한다 → 키를 바꿔 이 계정의 모든 기존 세션을 끊는다.
    if (email := read_session(request)):
        rotate_session_key(email)
        await hub.drop(email)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.post("/password")
async def password(body: PasswordChangeIn, request: Request, response: Response):
    email = require_user(request)
    if not allow(f"pw:{email}", 5, 600):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    try:
        change_password(email, body.current_password, body.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _set_session(request, response, email)       # 다른 기기는 끊기고 이 기기만 유지
    await hub.drop(email)                         # 이 기기 WS 는 새 쿠키로 자동 재접속
    return {"ok": True}


@router.post("/delete_account")
async def delete_account(body: PasswordIn, request: Request, response: Response):
    from .. import couples
    email = require_user(request)
    if not allow(f"pw:{email}", 5, 600):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    if not check_password(email, body.password):
        raise HTTPException(status_code=400, detail="invalid_credentials")
    for p in couples.delete_account(email):
        await hub.send(p, {"kind": "couple_unlinked", "by": email})
    await hub.drop(email)
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

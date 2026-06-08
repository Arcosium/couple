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
from ..config import settings

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
    partner = [e for e in settings.allowed_emails if e != email]
    return {
        "authenticated": True,
        "email": email,
        "partner": partner[0] if partner else None,
        "nickname_self": (
            settings.nickname_a if email == settings.allowed_emails[0] else settings.nickname_b
        ) if settings.allowed_emails else None,
        "nickname_partner": (
            settings.nickname_b if email == settings.allowed_emails[0] else settings.nickname_a
        ) if len(settings.allowed_emails) > 1 else None,
    }

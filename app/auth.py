import secrets
import smtplib
import time
from email.mime.text import MIMEText
from fastapi import Request, HTTPException
from itsdangerous import URLSafeSerializer, BadSignature

from .config import settings
from .db import cursor

SESSION_COOKIE = settings.session_cookie
# Cloudflare Access 가 인증을 통과시킬 때 origin 요청에 붙여주는 신원 헤더.
# Access 활성 상태에선 CF 가 이 값을 강제로 덮어쓰므로(클라이언트 위조 무시)
# origin 이 터널 너머에만 있는 한 신뢰 가능. 이게 사실상의 단일 로그인이다.
ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"
_serializer = URLSafeSerializer(settings.secret_key, salt="couple-auth")


def is_allowed(email: str) -> bool:
    email = email.lower().strip()
    if not email or "@" not in email:
        return False
    if settings.open_signup:
        return True
    return email in settings.allowed_emails


def _access_email(request) -> str | None:
    """Cloudflare Access 가 전달한, 화이트리스트에 속한 이메일(없으면 None).

    ⚠️ 보안 가정: 평문 Cf-Access 헤더를 신뢰한다. 이는 **couple.ai-ve.uk 에
    Cloudflare Access 가 켜져 있어 CF 가 이 헤더를 강제로 덮어써 줄 때만** 안전하다.
    Access 를 끄면 fail-open(원격 헤더 위조로 인증 우회)된다 — 이 트레이드오프는
    사장 확인 후 의도적으로 수용함(JWT(Cf-Access-Jwt-Assertion) 검증으로 fail-closed
    전환은 보류). origin 은 127.0.0.1:8800 바인드라 터널 외 직접 접근은 불가.
    """
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    email = headers.get(ACCESS_EMAIL_HEADER)
    if email and is_allowed(email):
        return email.lower().strip()
    return None


def issue_code(email: str) -> str:
    """6자리 코드 발급(10분 유효)."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = int(time.time()) + 600
    with cursor() as cur:
        cur.execute(
            "DELETE FROM login_codes WHERE email=? AND used=0",
            (email,),
        )
        cur.execute(
            "INSERT INTO login_codes (email, code, expires_ts, used) VALUES (?, ?, ?, 0)",
            (email, code, expires),
        )
    return code


def consume_code(email: str, code: str) -> bool:
    with cursor() as cur:
        row = cur.execute(
            "SELECT rowid, expires_ts, used FROM login_codes "
            "WHERE email=? AND code=? AND used=0 "
            "ORDER BY expires_ts DESC LIMIT 1",
            (email, code),
        ).fetchone()
        if not row:
            return False
        if row["expires_ts"] < int(time.time()):
            return False
        cur.execute("UPDATE login_codes SET used=1 WHERE rowid=?", (row["rowid"],))
        cur.execute(
            "INSERT INTO users (email) VALUES (?) ON CONFLICT(email) DO NOTHING",
            (email,),
        )
    return True


def deliver_code(email: str, code: str) -> dict:
    """SMTP 설정돼 있으면 이메일 발송, 아니면 콘솔 출력."""
    body = (
        f"커플 앱 로그인 코드: {code}\n"
        f"10분간 유효합니다. 본인이 요청한 게 아니라면 무시하세요. 💌"
    )
    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = "[커플앱] 로그인 코드"
        msg["From"] = settings.smtp_from or settings.smtp_user
        msg["To"] = email
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as srv:
                srv.starttls()
                srv.login(settings.smtp_user, settings.smtp_password)
                srv.sendmail(msg["From"], [email], msg.as_string())
            return {"sent": True, "channel": "email"}
        except Exception as exc:
            print(f"[auth] SMTP 실패: {exc} → 콘솔로 폴백")
    print(f"\n[LOGIN CODE] {email} → {code}\n")
    return {"sent": True, "channel": "console"}


def make_session_cookie(email: str) -> str:
    return _serializer.dumps({"email": email, "issued": int(time.time())})


def read_session(request: Request) -> str | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        try:
            data = _serializer.loads(tok)
        except BadSignature:
            data = None
        if data:
            email = data.get("email")
            if email and is_allowed(email):
                return email
    # 세션 쿠키가 없거나 무효면 Cloudflare Access 신원으로 폴백한다.
    return _access_email(request)


def require_user(request: Request) -> str:
    email = read_session(request)
    if not email:
        raise HTTPException(status_code=401, detail="not_authenticated")
    return email


def couple_of(email: str) -> int | None:
    with cursor() as cur:
        row = cur.execute("SELECT couple_id FROM users WHERE email=?",
                          (email.lower().strip(),)).fetchone()
    return row["couple_id"] if row and row["couple_id"] else None


def partner_of(email: str) -> str | None:
    from .db import couple_members
    cid = couple_of(email)
    if not cid:
        return None
    email = email.lower().strip()
    return next((m for m in couple_members(cid) if m != email), None)


def require_couple(request: Request) -> tuple[str, int]:
    """로그인+매칭 강제. 반환 (email, couple_id). 미매칭이면 409."""
    email = require_user(request)
    cid = couple_of(email)
    if not cid:
        raise HTTPException(status_code=409, detail="no_couple")
    return email, cid

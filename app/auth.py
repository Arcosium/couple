import re
import time
from fastapi import Request, HTTPException
from itsdangerous import URLSafeSerializer, BadSignature
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from .config import settings
from .db import cursor

SESSION_COOKIE = settings.session_cookie
_serializer = URLSafeSerializer(settings.secret_key, salt="couple-auth")

_ph = PasswordHasher()

USERNAME_MIN, USERNAME_MAX = 3, 32
PASSWORD_MIN = 8
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_username(u: str) -> str:
    u = (u or "").strip()
    if not (USERNAME_MIN <= len(u) <= USERNAME_MAX) or not _USERNAME_RE.match(u):
        raise ValueError("bad_username")
    return u.lower()


def validate_password(pw: str) -> None:
    if not pw or len(pw) < PASSWORD_MIN:
        raise ValueError("weak_password")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hash_: str, pw: str) -> bool:
    if not hash_:
        return False
    try:
        _ph.verify(hash_, pw)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ── 로그인 throttle (무차별 대입 방어) ────────────────────────────────
# CF Access 가 사라지면 로그인 폼이 공개되므로 필수. 단일 프로세스 uvicorn 전제의
# 인메모리 카운터(재시작 시 리셋). 정책(횟수·시간·키)은 보안 vs UX 트레이드오프다.
MAX_FAILS = 5
LOCK_SECONDS = 60
_attempts: dict[str, list] = {}   # username -> [fail_count, first_fail_ts]


def is_locked(username: str) -> bool:
    """해당 username 이 지금 잠금 상태인지. 잠금창이 지났으면 리셋하고 False."""
    rec = _attempts.get(username)
    if not rec:
        return False
    fails, first = rec
    if (time.time() - first) >= LOCK_SECONDS:
        _attempts.pop(username, None)        # 창 경과 → 리셋
        return False
    return fails >= MAX_FAILS


def record_failure(username: str) -> None:
    """로그인 실패 1건 기록. 잠금창이 지났으면 새 창으로 카운트 리셋."""
    now = time.time()
    rec = _attempts.get(username)
    if not rec or (now - rec[1]) >= LOCK_SECONDS:
        _attempts[username] = [1, now]
    else:
        rec[0] += 1


def clear_attempts(username: str) -> None:
    """로그인 성공 시 실패 기록 제거."""
    _attempts.pop(username, None)


def create_user(username: str, password: str) -> str:
    """신규 가입: email=username 행 생성. 반환 identity(email). 중복 시 ValueError."""
    username = validate_username(username)
    validate_password(password)
    ph = hash_password(password)
    with cursor() as cur:
        exists = cur.execute(
            "SELECT 1 FROM users WHERE email=? OR username=?", (username, username)
        ).fetchone()
        if exists:
            raise ValueError("username_taken")
        cur.execute(
            "INSERT INTO users (email, username, password_hash) VALUES (?, ?, ?)",
            (username, username, ph),
        )
    return username


def authenticate(username: str, password: str) -> str | None:
    """username 으로 행 조회 → 비번 검증 → identity(email) 반환, 실패 None."""
    username = (username or "").strip().lower()
    with cursor() as cur:
        row = cur.execute(
            "SELECT email, password_hash FROM users WHERE username=?", (username,)
        ).fetchone()
    if not row or not row["password_hash"]:
        return None
    if not verify_password(row["password_hash"], password):
        return None
    return row["email"]


def claim_legacy(email: str, username: str, password: str) -> str:
    """옛 email 행(username NULL)에 username/password_hash 세팅. 반환 identity(email).
    옛 데이터(couple/사진/일정)는 email 키 그대로라 자동 보존된다."""
    email = (email or "").strip().lower()
    username = validate_username(username)
    validate_password(password)
    ph = hash_password(password)
    with cursor() as cur:
        row = cur.execute("SELECT username FROM users WHERE email=?", (email,)).fetchone()
        if not row or row["username"] is not None:
            raise ValueError("not_claimable")          # 없음 or 이미 claim됨
        if cur.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("username_taken")
        cur.execute(
            "UPDATE users SET username=?, password_hash=? WHERE email=? AND username IS NULL",
            (username, ph, email),
        )
    return email


def make_session_cookie(email: str) -> str:
    return _serializer.dumps({"email": email, "issued": int(time.time())})


def read_session(request: Request) -> str | None:
    """서명된 세션 쿠키만 신뢰한다. 우리는 비밀번호 검증 후에만 쿠키를 발급하므로
    서명이 유효하면 신원으로 인정한다(헤더 폴백·화이트리스트 게이트 없음)."""
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    try:
        data = _serializer.loads(tok)
    except BadSignature:
        return None
    return data.get("email") if isinstance(data, dict) else None


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

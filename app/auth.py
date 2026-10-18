import re
import secrets
import time
from collections import deque
from fastapi import Request, HTTPException
from itsdangerous import URLSafeSerializer, BadSignature
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from .config import settings
from .db import cursor

SESSION_COOKIE = settings.session_cookie
SESSION_MAX_AGE = 60 * 60 * 24 * 90      # 쿠키 max_age 와 서버 검증에 함께 쓴다
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


# ponytail: 단일 프로세스 인메모리 슬라이딩 윈도(재시작 시 리셋). 워커를 늘리면 공유 저장소로.
_hits: dict[str, deque] = {}


def allow(key: str, limit: int, window: float) -> bool:
    """key 별로 window 초 안에 limit 회까지만 True. 공개 가입 후 한 사용자가
    가입·로그인·로컬 LLM·업로드를 독점하지 못하게 막는다."""
    now = time.time()
    if len(_hits) > 10_000:                       # 오래된 키 정리(메모리 상한)
        for k in [k for k, q in _hits.items() if not q or now - q[-1] >= 3600]:
            _hits.pop(k, None)
    q = _hits.setdefault(key, deque())
    while q and now - q[0] >= window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def client_ip(request: Request) -> str:
    """cloudflared 뒤에서는 CF-Connecting-IP(클라이언트가 위조 불가)를 쓴다."""
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")


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
        # 새 계정은 임의 session_key 로 시작 → 탈퇴한 같은 아이디의 옛 쿠키가 통하지 않는다.
        cur.execute(
            "INSERT INTO users (email, username, password_hash, session_key) VALUES (?, ?, ?, ?)",
            (username, username, ph, secrets.token_urlsafe(16)),
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


def _session_key(email: str):
    with cursor() as cur:
        row = cur.execute("SELECT session_key FROM users WHERE email=?", (email,)).fetchone()
    return row["session_key"] if row else False      # False = 계정 없음


def make_session_cookie(email: str) -> str:
    return _serializer.dumps({"email": email, "issued": int(time.time()),
                              "k": _session_key(email) or None})


def rotate_session_key(email: str) -> None:
    """이 계정의 모든 기존 쿠키를 무효화(로그아웃·비번 변경)."""
    with cursor() as cur:
        cur.execute("UPDATE users SET session_key=? WHERE email=?",
                    (secrets.token_urlsafe(16), email))


def read_session(request: Request) -> str | None:
    """서명 + 발급 90일 이내 + 계정 존재 + session_key 일치일 때만 신원으로 인정한다.
    키가 없던 기존 계정(NULL)은 키 없는 기존 쿠키와 일치해 재로그인 없이 유지된다."""
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    try:
        data = _serializer.loads(tok)
    except BadSignature:
        return None
    if not isinstance(data, dict) or not data.get("email"):
        return None
    if time.time() - int(data.get("issued") or 0) > SESSION_MAX_AGE:
        return None
    key = _session_key(data["email"])
    if key is False or key != data.get("k"):
        return None
    return data["email"]


def change_password(email: str, current: str, new: str) -> None:
    with cursor() as cur:
        row = cur.execute("SELECT password_hash FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(row["password_hash"], current):
        raise ValueError("invalid_credentials")
    validate_password(new)
    with cursor() as cur:
        cur.execute("UPDATE users SET password_hash=? WHERE email=?", (hash_password(new), email))
    rotate_session_key(email)


def check_password(email: str, pw: str) -> bool:
    with cursor() as cur:
        row = cur.execute("SELECT password_hash FROM users WHERE email=?", (email,)).fetchone()
    return bool(row) and verify_password(row["password_hash"], pw)


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

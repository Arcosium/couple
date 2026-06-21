# 자체 회원가입·로그인(아이디+비밀번호) 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cloudflare Access 헤더 신뢰를 제거하고, 앱 자체 아이디+비밀번호 회원가입/로그인 + 옛 계정 이어받기(claim)로 대체한다(옛 데이터 보존).

**Architecture:** 별칭 모델 — `users.email`(PK)은 불변 identity 로 유지(모든 데이터가 참조), `username`(신규, 로그인 키)과 `password_hash`(argon2id)를 더한다. 로그인은 username→email identity 로 해석. 레거시 행은 username 만 비워둔 채 존재하므로 claim 으로 이어받는다.

**Tech Stack:** FastAPI, SQLite, itsdangerous(서명 쿠키), argon2-cffi(비밀번호 해시), Alpine.js + Tailwind(로그인 폼), pytest.

## Global Constraints

- 테스트는 반드시 `python3.11 -m pytest` 로 실행(기본 `python` 은 import 단계에서 실패).
- 식별자(identity) 컬럼 rename 금지 — `email` PK 와 모든 `*_email`/`member_a/b` 는 그대로 둔다(별칭 모델).
- 세션 쿠키 옵션 고정: `max_age=60*60*24*90, httponly=True, samesite="lax", secure=False`(cloudflared 가 TLS 종단).
- 비밀번호는 평문 저장 금지 — argon2id(`argon2.PasswordHasher`) 만 사용.
- 운영 origin 은 `127.0.0.1` 바인드 유지(터널 전송은 그대로). CF Access 정책 해제는 코드 배포 후 마지막 단계.
- DB 마이그레이션은 멱등(`ALTER ... ADD COLUMN` 가드 + `CREATE ... IF NOT EXISTS`).
- 커밋은 각 Task 끝에서. 브랜치 `feat-password-auth`.

---

### Task 1: 비밀번호 해시 + 입력 검증 프리미티브

argon2 의존성 추가 + 순수 함수(DB 무관) 4종을 `app/auth.py` 에 추가. 기존 동작 불변(추가만).

**Files:**
- Modify: `requirements.txt`
- Modify: `app/auth.py` (상단 import + 새 함수)
- Test: `tests/test_password_primitives.py` (Create)

**Interfaces:**
- Produces:
  - `hash_password(pw: str) -> str`
  - `verify_password(hash_: str, pw: str) -> bool`
  - `validate_username(u: str) -> str` (정규화된 소문자 반환; 실패 시 `ValueError("bad_username")`)
  - `validate_password(pw: str) -> None` (실패 시 `ValueError("weak_password")`)
  - 상수 `USERNAME_MIN=3, USERNAME_MAX=32, PASSWORD_MIN=8`

- [ ] **Step 1: argon2-cffi 의존성 추가**

`requirements.txt` 끝에 한 줄 추가:
```
argon2-cffi==23.1.0
```
(이미 환경에 설치돼 있으나 명시적으로 고정한다.)

- [ ] **Step 2: 실패 테스트 작성**

Create `tests/test_password_primitives.py`:
```python
import pytest
from app import auth


def test_hash_and_verify_roundtrip():
    h = auth.hash_password("correct horse")
    assert h != "correct horse"          # 평문 아님
    assert auth.verify_password(h, "correct horse") is True
    assert auth.verify_password(h, "wrong") is False


def test_verify_handles_garbage_hash():
    assert auth.verify_password("", "x") is False
    assert auth.verify_password("not-a-hash", "x") is False


def test_validate_username_ok_normalizes_lower():
    assert auth.validate_username("Alice_01") == "alice_01"


@pytest.mark.parametrize("bad", ["", "ab", "a"*33, "has space", "한글", "no@at"])
def test_validate_username_rejects(bad):
    with pytest.raises(ValueError):
        auth.validate_username(bad)


def test_validate_password_min_length():
    auth.validate_password("12345678")          # ok, 8자
    with pytest.raises(ValueError):
        auth.validate_password("short")
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_password_primitives.py -v`
Expected: FAIL (`AttributeError: module 'app.auth' has no attribute 'hash_password'`)

- [ ] **Step 4: 프리미티브 구현**

`app/auth.py` 상단 import 영역에 추가(기존 import 아래):
```python
import re
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerifyError, InvalidHashError
```
그리고 `_serializer = ...` 줄 아래에 추가:
```python
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
    except (VerifyMismatchError, VerifyError, InvalidHashError):
        return False
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_password_primitives.py -v`
Expected: PASS (전부)

- [ ] **Step 6: 커밋**

```bash
git add requirements.txt app/auth.py tests/test_password_primitives.py
git commit -m "feat(auth): argon2 비밀번호 해시 + username/password 검증 프리미티브"
```

---

### Task 2: DB 스키마 — users.username, password_hash, 부분 유니크 인덱스

**Files:**
- Modify: `app/db.py` (SCHEMA `users` + `_migrate()`)
- Test: `tests/test_users_schema.py` (Create)

**Interfaces:**
- Produces: `users` 테이블에 `username TEXT`, `password_hash TEXT` 컬럼 + `idx_users_username` (partial unique, `WHERE username IS NOT NULL`).

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_users_schema.py`:
```python
from app.db import cursor


def test_users_has_new_columns():
    with cursor() as cur:
        cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
    assert "username" in cols
    assert "password_hash" in cols


def test_username_partial_unique_index_exists():
    with cursor() as cur:
        idx = {r["name"] for r in cur.execute("PRAGMA index_list(users)").fetchall()}
    assert "idx_users_username" in idx


def test_multiple_null_usernames_allowed():
    # 레거시 미claim 행이 여러 개여도(username=NULL) 충돌 없어야 한다.
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('legacy1@test')")
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('legacy2@test')")
        n = cur.execute(
            "SELECT COUNT(*) c FROM users WHERE email IN ('legacy1@test','legacy2@test')"
        ).fetchone()["c"]
    assert n == 2
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_users_schema.py -v`
Expected: FAIL (`username` not in cols)

- [ ] **Step 3: SCHEMA 수정**

`app/db.py` 의 `users` CREATE TABLE 를 다음으로 교체(컬럼 2개 추가):
```sql
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    nickname TEXT,
    avatar TEXT,
    last_seen TEXT,
    username TEXT,
    password_hash TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
    ON users (username) WHERE username IS NOT NULL;
```

- [ ] **Step 4: `_migrate()` 에 멱등 ALTER + 인덱스 추가**

`app/db.py::_migrate()` 안, `# 1) couple_id 컬럼` 블록 바로 뒤에 추가:
```python
        # users 에 username/password_hash (멱등) + 부분 유니크 인덱스
        user_cols = {r["name"] for r in cur.execute("PRAGMA table_info(users)").fetchall()}
        for col in ("username", "password_hash"):
            if col not in user_cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username "
                    "ON users (username) WHERE username IS NOT NULL")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_users_schema.py -v`
Expected: PASS

- [ ] **Step 6: 멱등성 확인 + 커밋**

Run(두 번 호출해도 에러 없어야): `python3.11 -c "from app.db import _migrate; _migrate(); _migrate(); print('idempotent ok')"`
Expected: `idempotent ok`
```bash
git add app/db.py tests/test_users_schema.py
git commit -m "feat(db): users.username/password_hash + 부분 유니크 인덱스(멱등 마이그레이션)"
```

---

### Task 3: 유저 스토어 — create_user / authenticate / claim_legacy

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_user_store.py` (Create)

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `validate_username`, `validate_password` (Task 1); `cursor` (db).
- Produces:
  - `create_user(username: str, password: str) -> str` — email=username 행 생성, identity(email) 반환. 중복 시 `ValueError("username_taken")`.
  - `authenticate(username: str, password: str) -> str | None` — 성공 시 identity(email), 실패 None.
  - `claim_legacy(email: str, username: str, password: str) -> str` — 레거시 행에 username/hash 세팅, identity(email) 반환. 불가 시 `ValueError("not_claimable")` / `ValueError("username_taken")`.

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_user_store.py`:
```python
import pytest
from app import auth
from app.db import cursor


def test_create_user_sets_email_equals_username():
    ident = auth.create_user("storeuser1", "pw12345678")
    assert ident == "storeuser1"
    with cursor() as cur:
        row = cur.execute("SELECT email, username, password_hash FROM users "
                          "WHERE username='storeuser1'").fetchone()
    assert row["email"] == "storeuser1"
    assert row["password_hash"]


def test_create_user_duplicate_rejected():
    auth.create_user("dupuser", "pw12345678")
    with pytest.raises(ValueError):
        auth.create_user("dupuser", "pw12345678")


def test_authenticate_good_and_bad():
    auth.create_user("loginuser", "pw12345678")
    assert auth.authenticate("loginuser", "pw12345678") == "loginuser"
    assert auth.authenticate("loginuser", "wrongpass") is None
    assert auth.authenticate("ghost", "whatever") is None


def test_claim_legacy_preserves_identity():
    # 레거시 행(username NULL) 시드
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email, couple_id) VALUES ('old@x.com', 1)")
    ident = auth.claim_legacy("old@x.com", "claimed01", "pw12345678")
    assert ident == "old@x.com"                       # identity 는 옛 email 유지
    assert auth.authenticate("claimed01", "pw12345678") == "old@x.com"
    with cursor() as cur:
        row = cur.execute("SELECT couple_id FROM users WHERE email='old@x.com'").fetchone()
    assert row["couple_id"] == 1                       # 옛 데이터 연결 보존


def test_claim_rejects_already_claimed_and_missing():
    with cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO users (email) VALUES ('once@x.com')")
    auth.claim_legacy("once@x.com", "onceclaim", "pw12345678")
    with pytest.raises(ValueError):                    # 이미 claim 됨
        auth.claim_legacy("once@x.com", "another", "pw12345678")
    with pytest.raises(ValueError):                    # 존재하지 않는 email
        auth.claim_legacy("nobody@x.com", "x", "pw12345678")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_user_store.py -v`
Expected: FAIL (`create_user` 없음)

- [ ] **Step 3: 구현**

`app/auth.py` 의 프리미티브(Task 1) 아래에 추가:
```python
def create_user(username: str, password: str) -> str:
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_user_store.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/auth.py tests/test_user_store.py
git commit -m "feat(auth): create_user/authenticate/claim_legacy (별칭 모델, 데이터 보존)"
```

---

### Task 4: 로그인 throttle (무차별 대입 방어)

> 💡 **학습 기여 지점**: 이 throttle 정책(잠금 횟수·시간·키 선정)은 보안 vs UX 트레이드오프가 있는 지점입니다. 실행 단계에서 직접 구현해 보고 싶으면, 아래 레퍼런스를 가이드로 삼아 정책을 바꿔도 됩니다(예: username+IP 키로 표적 잠금 DoS 완화). 기본 레퍼런스는 username 키·5회·60초입니다.

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_login_throttle.py` (Create)

**Interfaces:**
- Produces:
  - `is_locked(username: str) -> bool`
  - `record_failure(username: str) -> None`
  - `clear_attempts(username: str) -> None`
  - 상수 `MAX_FAILS=5, LOCK_SECONDS=60`
- Note: 인메모리(`_attempts` dict). 단일 프로세스 uvicorn 전제, 재시작 시 리셋.

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_login_throttle.py`:
```python
from app import auth


def setup_function():
    auth._attempts.clear()


def test_locks_after_max_fails():
    for _ in range(auth.MAX_FAILS):
        assert auth.is_locked("victim") is False
        auth.record_failure("victim")
    assert auth.is_locked("victim") is True


def test_clear_resets():
    for _ in range(auth.MAX_FAILS):
        auth.record_failure("v2")
    auth.clear_attempts("v2")
    assert auth.is_locked("v2") is False


def test_window_expiry_unlocks(monkeypatch):
    import app.auth as a
    t = [1000.0]
    monkeypatch.setattr(a.time, "time", lambda: t[0])
    for _ in range(a.MAX_FAILS):
        a.record_failure("v3")
    assert a.is_locked("v3") is True
    t[0] += a.LOCK_SECONDS + 1            # 잠금창 경과
    assert a.is_locked("v3") is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_login_throttle.py -v`
Expected: FAIL (`_attempts` 없음)

- [ ] **Step 3: 구현 (레퍼런스)**

`app/auth.py` 에 추가:
```python
MAX_FAILS = 5
LOCK_SECONDS = 60
_attempts: dict[str, list] = {}   # username -> [fail_count, first_fail_ts]


def is_locked(username: str) -> bool:
    rec = _attempts.get(username)
    if not rec:
        return False
    fails, first = rec
    if (time.time() - first) >= LOCK_SECONDS:
        _attempts.pop(username, None)        # 창 경과 → 리셋
        return False
    return fails >= MAX_FAILS


def record_failure(username: str) -> None:
    now = time.time()
    rec = _attempts.get(username)
    if not rec or (now - rec[1]) >= LOCK_SECONDS:
        _attempts[username] = [1, now]
    else:
        rec[0] += 1


def clear_attempts(username: str) -> None:
    _attempts.pop(username, None)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_login_throttle.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/auth.py tests/test_login_throttle.py
git commit -m "feat(auth): 인메모리 로그인 throttle(5회/60초)"
```

---

### Task 5: 설정 플래그 (allow_signup / allow_legacy_claim)

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_auth_flags.py` (Create)

**Interfaces:**
- Produces: `settings.allow_signup: bool`(기본 True), `settings.allow_legacy_claim: bool`(기본 True).

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_auth_flags.py`:
```python
from app.config import settings


def test_flags_default_true():
    assert settings.allow_signup is True
    assert settings.allow_legacy_claim is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_auth_flags.py -v`
Expected: FAIL (`allow_signup` 없음)

- [ ] **Step 3: 구현**

`app/config.py` 의 `open_signup` 줄 아래에 추가:
```python
    allow_signup: bool = os.getenv("ALLOW_SIGNUP", "1").lower() in ("1", "true", "yes")
    allow_legacy_claim: bool = os.getenv("ALLOW_LEGACY_CLAIM", "1").lower() in ("1", "true", "yes")
```
그리고 `open_signup` / `allowed_emails` 줄에 주석 추가:
```python
    # DEPRECATED(2026-06-21): CF Access·화이트리스트 폐기. conftest 시드에서만 참조.
```
(필드 자체는 conftest 가 쓰므로 삭제하지 않는다.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3.11 -m pytest tests/test_auth_flags.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/config.py tests/test_auth_flags.py
git commit -m "feat(config): allow_signup/allow_legacy_claim 플래그(레거시 설정 deprecate)"
```

---

### Task 6: 인증 라우트 — /signup /login /claim (OTP 라우트 제거)

**Files:**
- Modify: `app/routes/auth_routes.py` (전체 재작성)
- Test: `tests/test_auth_routes_password.py` (Create)

**Interfaces:**
- Consumes: `create_user/authenticate/claim_legacy`(Task 3), `is_locked/record_failure/clear_attempts`(Task 4), `settings.allow_signup/allow_legacy_claim`(Task 5), `SESSION_COOKIE/make_session_cookie/read_session`.
- Produces 엔드포인트: `POST /api/auth/signup`, `/login`, `/claim`, `/logout`, `GET /api/auth/me`.
- Note: `read_session` 헤더 폴백은 Task 8 에서 제거되므로, 이 Task 시점엔 기존 테스트(Cf-Access 헤더)가 아직 통과한다.

- [ ] **Step 1: 실패 테스트 작성**

Create `tests/test_auth_routes_password.py`:
```python
from fastapi.testclient import TestClient
from server import app
from app.config import settings


def _client():
    return TestClient(app)            # 인스턴스마다 독립 쿠키 jar


def test_signup_then_me_authenticated():
    c = _client()
    r = c.post("/api/auth/signup", json={"username": "routeA", "password": "pw12345678"})
    assert r.status_code == 200 and r.json()["email"] == "routea"
    me = c.get("/api/auth/me")        # 쿠키 자동 동봉
    assert me.json()["authenticated"] is True


def test_login_wrong_password():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeB", "password": "pw12345678"})
    bad = c.post("/api/auth/login", json={"username": "routeB", "password": "nope!!!!"})
    assert bad.status_code == 401


def test_signup_duplicate_400():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeC", "password": "pw12345678"})
    dup = c.post("/api/auth/signup", json={"username": "routeC", "password": "pw12345678"})
    assert dup.status_code == 400 and dup.json()["detail"] == "username_taken"


def test_signup_closed_403(monkeypatch):
    monkeypatch.setattr(settings, "allow_signup", False)
    c = _client()
    r = c.post("/api/auth/signup", json={"username": "routeD", "password": "pw12345678"})
    assert r.status_code == 403


def test_claim_preserves_legacy_couple():
    # conftest 가 couple #1 을 a@test/b@test 로 시드. a@test 는 username NULL → claim 가능.
    legacy = (settings.allowed_emails + ["a@test"])[0]
    c = _client()
    r = c.post("/api/auth/claim",
               json={"email": legacy, "username": "claimeduser", "password": "pw12345678"})
    assert r.status_code == 200
    me = c.get("/api/auth/me").json()
    assert me["authenticated"] is True and me["matched"] is True   # 커플 #1 보존


def test_logout_clears_session():
    c = _client()
    c.post("/api/auth/signup", json={"username": "routeE", "password": "pw12345678"})
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json()["authenticated"] is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_auth_routes_password.py -v`
Expected: FAIL (404 — signup 라우트 없음)

- [ ] **Step 3: `auth_routes.py` 전체 재작성**

`app/routes/auth_routes.py` 전체를 다음으로 교체. **`me()` 함수 본문은 현재 파일(라인 63~90)을 그대로 보존**한다(아래에 동일하게 포함):
```python
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
```

- [ ] **Step 4: 테스트 통과 확인 (신규 + 기존 회귀)**

Run: `python3.11 -m pytest tests/test_auth_routes_password.py -v`
Expected: PASS
Run(기존도 아직 green): `python3.11 -m pytest -q`
Expected: PASS (기존 테스트는 Cf-Access 헤더 폴백이 아직 살아 있어 통과)

- [ ] **Step 5: 커밋**

```bash
git add app/routes/auth_routes.py tests/test_auth_routes_password.py
git commit -m "feat(auth): /signup /login /claim 라우트(OTP request_code/verify 제거)"
```

---

### Task 7: 테스트 인증을 쿠키 방식으로 전환 (8개 파일)

헤더 신뢰 제거(Task 8) 전에, 기존 테스트들이 Cf-Access 헤더 대신 **서명 세션 쿠키**로 인증하도록 바꾼다. 이 시점엔 헤더 폴백도 살아 있어 쿠키/헤더 둘 다 통과 → green 유지.

**Files (Modify):** `tests/test_calendar_multiday.py`, `tests/test_kakao_import.py`, `tests/test_notes.py`, `tests/test_birthday_dday.py`, `tests/test_couple_routes.py`, `tests/test_match_gating.py`, `tests/test_tenant_isolation.py`
- (`tests/test_access_auth.py` 는 Task 8 에서 통째 교체하므로 여기선 건드리지 않음)

**Interfaces:**
- Consumes: `app.auth.make_session_cookie`, `app.auth.SESSION_COOKIE`.
- 패턴: 식별자 `e` 에 대해 `{"Cookie": f"{SESSION_COOKIE}={make_session_cookie(e)}"}` 를 `headers=` 로 넘긴다.

- [ ] **Step 1: `def _h(e)` 형태 3개 파일 교체**

`tests/test_kakao_import.py:60`, `tests/test_notes.py:9`, `tests/test_match_gating.py:9` — 각 파일의
```python
def _h(e): return {"Cf-Access-Authenticated-User-Email": e}
```
를 다음으로 교체(같은 파일 import 영역에 두 import 가 없으면 추가):
```python
from app.auth import SESSION_COOKIE, make_session_cookie
def _h(e): return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(e)}"}
```

- [ ] **Step 2: 헬퍼 함수 형태 2개 파일 교체**

`tests/test_couple_routes.py:9`, `tests/test_tenant_isolation.py:20` — 헬퍼의
```python
    return {"Cf-Access-Authenticated-User-Email": email}
```
를 다음으로 교체(파일 상단에 import 추가):
```python
    return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(email)}"}
```
import 추가(각 파일 상단):
```python
from app.auth import SESSION_COOKIE, make_session_cookie
```

- [ ] **Step 3: 상수/인라인 형태 2개 파일 교체**

`tests/test_calendar_multiday.py:12`:
```python
H = {"Cf-Access-Authenticated-User-Email": settings.allowed_emails[0]}
```
→ (상단에 import 추가 후)
```python
from app.auth import SESSION_COOKIE, make_session_cookie
H = {"Cookie": f"{SESSION_COOKIE}={make_session_cookie((settings.allowed_emails + ['a@test'])[0])}"}
```

`tests/test_birthday_dday.py:44` 인라인:
```python
        headers={"Cf-Access-Authenticated-User-Email": settings.allowed_emails[0]},
```
→ (상단에 import 추가 후)
```python
        headers={"Cookie": f"{SESSION_COOKIE}={make_session_cookie((settings.allowed_emails + ['a@test'])[0])}"},
```
import 추가(파일 상단):
```python
from app.auth import SESSION_COOKIE, make_session_cookie
```

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `python3.11 -m pytest -q`
Expected: PASS (쿠키 인증으로 동작, 헤더 폴백도 아직 살아 있음)

- [ ] **Step 5: 커밋**

```bash
git add tests/test_calendar_multiday.py tests/test_kakao_import.py tests/test_notes.py \
        tests/test_birthday_dday.py tests/test_couple_routes.py tests/test_match_gating.py \
        tests/test_tenant_isolation.py
git commit -m "test: Cf-Access 헤더 인증 → 서명 세션 쿠키 인증으로 전환"
```

---

### Task 8: 헤더 신뢰·OTP·is_allowed 제거 + read_session 재작성

이번 작업의 **핵심 보안 변경**. fail-open 폴백을 끊는다. Task 7 로 테스트가 이미 쿠키 인증이라 green 유지.

**Files:**
- Modify: `app/auth.py` (제거 + read_session 재작성)
- Rewrite: `tests/test_access_auth.py` → 내용 교체(파일명 유지)
- Modify: `tests/test_auth_couple.py` (is_allowed 테스트 제거)

**Interfaces:**
- `read_session(request) -> str | None` — **서명 쿠키만** 검증.
- 제거: `_access_email`, `ACCESS_EMAIL_HEADER`, `is_allowed`, `issue_code`, `consume_code`, `deliver_code`, 관련 `smtplib`/`MIMEText`/`secrets` import.

- [ ] **Step 1: 실패 테스트 작성 (test_access_auth.py 통째 교체)**

`tests/test_access_auth.py` 전체를 교체:
```python
"""CF Access 헤더는 더 이상 신뢰하지 않는다(자체 비밀번호 인증으로 전환).
헤더만으로는 인증되지 않아야 하고, 세션 쿠키로만 인증된다.
"""
from fastapi.testclient import TestClient
from server import app

ACCESS_HEADER = "Cf-Access-Authenticated-User-Email"


def _client():
    return TestClient(app)


def test_access_header_no_longer_authenticates():
    c = _client()
    r = c.get("/api/auth/me", headers={ACCESS_HEADER: "stranger@example.com"})
    assert r.json()["authenticated"] is False        # 헤더 위조 차단(fail-closed)


def test_no_cookie_is_unauthenticated():
    assert _client().get("/api/auth/me").json()["authenticated"] is False


def test_cookie_session_authenticates():
    c = _client()
    c.post("/api/auth/signup", json={"username": "hdru1", "password": "pw12345678"})
    assert c.get("/api/auth/me").json()["authenticated"] is True


def test_index_serves_login_when_only_header_present():
    c = _client()
    anon = c.get("/", headers={ACCESS_HEADER: "stranger@example.com"}).text
    assert "회원가입" in anon or "로그인" in anon       # login.html 마커(Task 9 이후 보강)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3.11 -m pytest tests/test_access_auth.py -v`
Expected: FAIL (`test_access_header_no_longer_authenticates` — 아직 헤더로 인증됨)

- [ ] **Step 3: `app/auth.py` 정리 — 제거**

다음을 삭제한다:
- 상단 `import secrets`, `import smtplib`, `from email.mime.text import MIMEText` (Task 1 에서 추가한 argon2/re import 는 유지).
- `ACCESS_EMAIL_HEADER = ...` 줄.
- 함수 전체: `is_allowed`, `_access_email`, `issue_code`, `consume_code`, `deliver_code`.
- `ACCESS_EMAIL_HEADER` 위에 있던 CF Access 설명 주석 블록.

- [ ] **Step 4: `read_session` 재작성**

`app/auth.py::read_session` 를 다음으로 교체:
```python
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
```

- [ ] **Step 5: `tests/test_auth_couple.py` 의 is_allowed 테스트 제거**

`test_open_signup_allows_any_email` 과 `test_closed_signup_keeps_whitelist` 두 함수를 삭제한다(`is_allowed` 폐기). `test_couple_of_and_partner` 는 유지. 파일 상단 `from app.config import settings` 가 남은 테스트에서 안 쓰이면 제거.

- [ ] **Step 6: 전체 테스트 통과 확인**

Run: `python3.11 -m pytest -q`
Expected: PASS (전체)

- [ ] **Step 7: 잔여 참조 없음 확인**

Run: `grep -rn "is_allowed\|_access_email\|ACCESS_EMAIL_HEADER\|issue_code\|consume_code\|deliver_code" app/ server.py`
Expected: 출력 없음(빈 결과)

- [ ] **Step 8: 커밋**

```bash
git add app/auth.py tests/test_access_auth.py tests/test_auth_couple.py
git commit -m "feat(auth): CF Access 헤더 신뢰·OTP·화이트리스트 제거 — read_session 은 서명 쿠키만(fail-closed)"
```

---

### Task 9: 로그인 화면 + 프론트 로그아웃

**Files:**
- Modify: `templates/login.html` (폼 영역 + 스크립트 교체)
- Modify: `static/js/app.js` (`logout()`)

**Interfaces:**
- Consumes: `POST /api/auth/login`, `/signup`, `/claim`, `/logout`(Task 6,8).
- pytest 없음 — 서버 기동 후 수동/Playwright 검증.

- [ ] **Step 1: `login.html` 폼 영역 교체**

`templates/login.html` 에서 `<div x-data="loginForm()" class="space-y-5">` 부터 그 닫는 `</div>`(현재 라인 91~140, step1/step2 template + 에러 `<p>` 포함)까지를 다음으로 교체:
```html
    <div x-data="loginForm()" class="space-y-5">
      <!-- 모드 탭 -->
      <div class="flex gap-1 bg-rose-100/60 rounded-2xl p-1 text-sm">
        <button type="button" @click="mode='login'"  :class="mode==='login'  ? 'bg-white shadow text-rose-700' : 'text-rose-500'" class="flex-1 py-2 rounded-xl font-medium transition">로그인</button>
        <button type="button" @click="mode='signup'" :class="mode==='signup' ? 'bg-white shadow text-rose-700' : 'text-rose-500'" class="flex-1 py-2 rounded-xl font-medium transition">회원가입</button>
        <button type="button" @click="mode='claim'"  :class="mode==='claim'  ? 'bg-white shadow text-rose-700' : 'text-rose-500'" class="flex-1 py-2 rounded-xl font-medium transition">이어받기</button>
      </div>

      <form @submit.prevent="submit" class="space-y-4">
        <!-- 이어받기: 옛 이메일 -->
        <label class="block" x-show="mode==='claim'">
          <span class="block text-sm font-medium text-rose-900/80 mb-1.5">예전에 쓰던 이메일</span>
          <input type="email" x-model="claimEmail" autocomplete="email" placeholder="your@email.com"
            class="w-full bg-white/80 rounded-2xl px-4 py-3.5 ring-1 ring-rose-200 focus:ring-2 focus:ring-rose-400 outline-none text-rose-900 placeholder:text-rose-300 transition" />
          <span class="block mt-1 text-xs text-rose-600/70">사진·일정·기념일을 그대로 이어받아요 💝</span>
        </label>

        <label class="block">
          <span class="block text-sm font-medium text-rose-900/80 mb-1.5">아이디</span>
          <input type="text" x-model="username" required autocomplete="username" placeholder="3~32자 영문/숫자"
            class="w-full bg-white/80 rounded-2xl px-4 py-3.5 ring-1 ring-rose-200 focus:ring-2 focus:ring-rose-400 outline-none text-rose-900 placeholder:text-rose-300 transition" />
        </label>

        <label class="block">
          <span class="block text-sm font-medium text-rose-900/80 mb-1.5">비밀번호</span>
          <input type="password" x-model="password" required autocomplete="current-password" placeholder="8자 이상"
            class="w-full bg-white/80 rounded-2xl px-4 py-3.5 ring-1 ring-rose-200 focus:ring-2 focus:ring-rose-400 outline-none text-rose-900 placeholder:text-rose-300 transition" />
        </label>

        <label class="block" x-show="mode==='signup'">
          <span class="block text-sm font-medium text-rose-900/80 mb-1.5">비밀번호 확인</span>
          <input type="password" x-model="password2" autocomplete="new-password" placeholder="다시 한 번"
            class="w-full bg-white/80 rounded-2xl px-4 py-3.5 ring-1 ring-rose-200 focus:ring-2 focus:ring-rose-400 outline-none text-rose-900 placeholder:text-rose-300 transition" />
        </label>

        <button type="submit" :disabled="loading"
          class="w-full bg-gradient-to-br from-rose-400 to-pink-500 hover:from-rose-500 hover:to-pink-600 text-white font-semibold py-3.5 rounded-2xl shadow-lg shadow-rose-300/40 active:scale-[.98] transition disabled:opacity-50">
          <span x-show="!loading" x-text="mode==='login' ? '💖 들어가기' : (mode==='signup' ? '💌 가입하기' : '🎁 이어받기')"></span>
          <span x-show="loading">잠시만…</span>
        </button>
      </form>

      <p x-show="error" x-text="error"
        class="text-rose-700 bg-rose-100 rounded-xl px-3 py-2 text-sm text-center"></p>
    </div>
```

- [ ] **Step 2: `login.html` 스크립트 교체**

`templates/login.html` 하단 `<script>function loginForm() {...}</script>` 의 `loginForm` 정의를 다음으로 교체(`<script src=".../alpine.min.js" defer></script>` 줄은 유지):
```html
<script>
function loginForm() {
  return {
    mode: 'login', username: '', password: '', password2: '', claimEmail: '',
    loading: false, error: '',
    msg(code) {
      return ({
        invalid_credentials: '아이디 또는 비밀번호가 달라',
        username_taken: '이미 쓰이는 아이디야',
        bad_username: '아이디는 3~32자 영문/숫자/._- 만 돼',
        weak_password: '비밀번호는 8자 이상이어야 해',
        not_claimable: '이어받을 수 없는 이메일이야',
        signup_closed: '지금은 가입을 받지 않아',
        claim_closed: '이어받기는 마감됐어',
        too_many_attempts: '시도가 많아 잠깐 잠겼어. 1분 뒤 다시 해줘',
      })[code] || '문제가 생겼어. 잠시 후 다시';
    },
    async submit() {
      this.error = '';
      if (this.mode === 'signup' && this.password !== this.password2) {
        this.error = '비밀번호 확인이 달라'; return;
      }
      this.loading = true;
      try {
        let url, payload;
        if (this.mode === 'login') { url = '/api/auth/login'; payload = { username: this.username, password: this.password }; }
        else if (this.mode === 'signup') { url = '/api/auth/signup'; payload = { username: this.username, password: this.password }; }
        else { url = '/api/auth/claim'; payload = { email: this.claimEmail, username: this.username, password: this.password }; }
        const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(this.msg(j.detail));
        location.href = '/';
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
  }
}
</script>
```

- [ ] **Step 3: `app.js` logout 교체**

`static/js/app.js` 의 `logout()`(현재 라인 299~302):
```javascript
    logout() {
      // Cloudflare Access 세션을 종료한다(앱 자체 쿠키만 지워선 헤더로 즉시 재인증됨).
      location.href = '/cdn-cgi/access/logout';
    },
```
를 다음으로 교체:
```javascript
    async logout() {
      // 앱 세션 쿠키 삭제 후 로그인 화면으로. (CF Access 의존 제거)
      try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_) {}
      location.href = '/';
    },
```

- [ ] **Step 4: 수동 검증 (서버 기동)**

Run(백그라운드): `COUPLE_DB=/tmp/coco_verify.db python3.11 -m uvicorn server:app --host 127.0.0.1 --port 8899 &`
그리고:
```bash
sleep 2
curl -s localhost:8899/ | grep -q "회원가입" && echo "LOGIN PAGE OK"
# 가입 → 쿠키 → me
curl -s -c /tmp/cj -X POST localhost:8899/api/auth/signup -H 'Content-Type: application/json' -d '{"username":"smoke1","password":"pw12345678"}'
curl -s -b /tmp/cj localhost:8899/api/auth/me
```
Expected: `LOGIN PAGE OK`, signup `{"ok":true,...}`, me `"authenticated":true`. 확인 후 서버 종료(`kill %1`), `/tmp/coco_verify.db` 삭제.

> ⚠️ 검증 시 반드시 `COUPLE_DB` 를 임시 경로로 지정 — 운영 `data/couple.db` 를 건드리지 말 것(메모리 ops-hazard 참조).

- [ ] **Step 5: 커밋**

```bash
git add templates/login.html static/js/app.js
git commit -m "feat(ui): 로그인/회원가입/이어받기 폼 + 자체 로그아웃(CF Access 폼 제거)"
```

---

### Task 10: 주변 정리 — server 주석, 모바일 호스트, 배포 문서

**Files:**
- Modify: `server.py` (WS 주석)
- Modify: `mobile/app/src/main/java/uk/aive/couple/MainActivity.kt` (`isInAppHost`)
- Modify: `deploy/couple.service` (주석/Env)
- Modify: `deploy/BETA.md` (CF Access 언급)

**Interfaces:** 동작 변경 없음(주석·문서·모바일 호스트 화이트리스트 축소). APK 재빌드는 선택.

- [ ] **Step 1: `server.py` WS 주석 정리**

`server.py` 라인 141 의 주석:
```python
    # read_session 이 쿠키뿐 아니라 Cf-Access 헤더 폴백도 보므로 headers 도 넘긴다.
```
를 다음으로 교체:
```python
    # read_session 은 서명 세션 쿠키만 본다(헤더 폴백 제거). 쿠키 파싱용으로 구성.
```

- [ ] **Step 2: 모바일 `isInAppHost` 축소**

`MainActivity.kt` 의 `isInAppHost`(라인 235~243)를 다음으로 교체:
```kotlin
    /** WebView 안에 머물러야 하는 호스트(앱 도메인만 — 자체 로그인으로 외부 IdP 불필요). */
    private fun isInAppHost(host: String?): Boolean {
        val h = (host ?: return false).lowercase()
        return h == "couple.ai-ve.uk" || h.endsWith(".couple.ai-ve.uk")
    }
```

- [ ] **Step 3: `deploy/couple.service` 주석/Env 수정**

`deploy/couple.service` 의 `OPEN_SIGNUP` 관련 주석 블록:
```
# 멀티유저 개방: 앱 화이트리스트 해제 → 유일한 게이트는 Cloudflare Access(현재 2이메일,
# 추후 전체 이메일+OTP 로 확대 예정). CF Access 는 절대 끄지 말 것(끄면 헤더 위조 fail-open).
Environment=OPEN_SIGNUP=1
```
를 다음으로 교체:
```
# 인증은 앱 자체(아이디+비밀번호, argon2)가 담당. Cloudflare Access 없이도 fail-closed.
# CF Access 정책은 해제 가능(터널은 전송용으로 유지). 가입 차단은 ALLOW_SIGNUP=0.
# 두 분이 옛 계정 이어받기(claim)를 끝내면 ALLOW_LEGACY_CLAIM=0 로 닫을 것.
Environment=SECRET_KEY=__강한무작위값으로_교체__
```
> `SECRET_KEY` 는 실제로는 `/home/opc/projects/.env` 에서 로드되므로, service 파일엔 주석성 안내로 두거나 .env 에 강한 값이 있는지 확인. 운영 `.env` 에 `SECRET_KEY` 가 없거나 `dev-insecure-change-me` 면 강한 값으로 교체(기존 세션 무효화됨).

- [ ] **Step 4: `deploy/BETA.md` 갱신**

`deploy/BETA.md` 에서 CF Access 를 "유일한 게이트/필수"로 설명하는 문구를, "앱 자체 아이디+비밀번호 인증으로 대체, CF Access 선택" 으로 수정(해당 문단 1~2곳).

- [ ] **Step 5: 회귀 확인 + 커밋**

Run: `python3.11 -m pytest -q`
Expected: PASS (코드 동작 무변경)
```bash
git add server.py mobile/app/src/main/java/uk/aive/couple/MainActivity.kt deploy/couple.service deploy/BETA.md
git commit -m "chore(auth): CF Access 의존 흔적 정리(서버 주석·모바일 호스트·배포 문서)"
```

---

### Task 11: 최종 검증 + 롤아웃 노트

**Files:** 없음(검증). 필요 시 `docs/superpowers/specs/2026-06-21-password-auth-migration-design.md` 상태 갱신.

- [ ] **Step 1: 전체 테스트 green**

Run: `python3.11 -m pytest -q`
Expected: PASS (전부). 실패 시 해당 Task 로 돌아가 수정.

- [ ] **Step 2: 잔여 CF Access 참조 점검**

Run: `grep -rn -i "cf-access\|cf_access\|cdn-cgi/access\|access_email\|ACCESS_EMAIL" app/ server.py static/ templates/`
Expected: 의미 있는 코드 참조 없음(테스트 설명 주석 정도만 허용).

- [ ] **Step 3: 롤아웃 체크리스트(사장 실행, 문서화만)**

배포 순서(사장이 승인·실행):
1. 운영 `.env` 의 `SECRET_KEY` 가 강한 무작위값인지 확인(아니면 교체 → 기존 세션 무효).
2. `feat-password-auth` 머지 → `sudo systemctl restart couple.service`.
3. 사이트 접속 → "이어받기" 로 두 분(옛 이메일)이 username/비밀번호 설정 → 사진·일정 보존 확인.
4. 둘 다 이어받기 끝나면 `ALLOW_LEGACY_CLAIM=0` 설정 후 재시작(이어받기 창 닫기).
5. Cloudflare Access 정책 해제(완전 개방). 터널은 유지.
6. (선택) 모바일 APK 재빌드(`isInAppHost` 축소 반영).

- [ ] **Step 4: 스펙 상태 갱신 + 커밋**

스펙 문서 상태 줄을 `상태: 구현 완료` 로 갱신.
```bash
git add docs/superpowers/specs/2026-06-21-password-auth-migration-design.md
git commit -m "docs(auth): 비밀번호 인증 전환 구현 완료 — 스펙 상태 갱신"
```

---

## Self-Review

**1. Spec coverage** (스펙 섹션 → Task 매핑):
- 별칭 모델/identity 분리 → Task 2,3 ✓
- `app/auth.py` 제거/추가/read_session → Task 1,3,4,8 ✓
- throttle → Task 4 ✓
- 라우트 signup/login/claim/logout/me → Task 6 ✓
- DB 스키마 → Task 2 ✓
- config 플래그 → Task 5 ✓
- login.html → Task 9 ✓
- app.js logout → Task 9 ✓
- server.py WS → Task 10 ✓
- 모바일 host → Task 10 ✓
- 배포/문서 → Task 10, 11 ✓
- 테스트(쿠키 헬퍼·8파일·test_access_auth 재작성·test_auth_couple 정리) → Task 7,8 ✓
- 데이터 보존(claim) → Task 3,6 ✓
- 비목표(비번 재설정/2FA/rename) → 미포함(의도적) ✓

**2. Placeholder scan:** "TBD/TODO/적절히 처리" 류 없음. 모든 코드 스텝에 실제 코드·명령·기대출력 포함. (Task 10 의 `SECRET_KEY=__강한무작위값으로_교체__` 는 사장이 채울 비밀값 자리로, 의도적 안내.)

**3. Type consistency:**
- `create_user/authenticate/claim_legacy` 반환 = identity(email: str) — Task 3 정의와 Task 6 사용 일치 ✓
- `read_session` 반환 `str | None` — Task 8 정의, `me`/`require_user`/WS 사용 일치 ✓
- throttle: `is_locked/record_failure/clear_attempts` 시그니처 — Task 4 정의, Task 6 사용 일치 ✓
- 세션 쿠키 키 `SESSION_COOKIE` + `make_session_cookie(email)` — auth.py(유지)·라우트·테스트 헬퍼 전부 동일 ✓
- 에러 detail 문자열(`username_taken`/`invalid_credentials`/`not_claimable`/`weak_password`/`bad_username`/`signup_closed`/`claim_closed`/`too_many_attempts`) — 라우트(Task 6)와 프론트 `msg()`(Task 9) 키 일치 ✓

# 멀티커플 (로그인·세션분리·초대/수락/해제·카카오 일괄가져오기) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단일커플 전용 앱을 멀티커플(테넌트 격리)로 전환하고, 이메일 초대→수락으로 커플을 맺고 해제하며, 카카오맵 저장 폴더를 공유 링크로 일괄 가져오는 기능을 `beta` 브랜치/별도 서비스에서 구현한다.

**Architecture:** 모든 데이터 테이블에 `couple_id` 를 추가하고 모든 쿼리를 `WHERE couple_id=?` 로 스코프한다. `couples`/`couple_invites` 신규 테이블이 매칭 상태를 표현한다. prod와 beta는 **동일 코드**를 공유하고 환경변수(`COUPLE_DB`·`SESSION_COOKIE`·`OPEN_SIGNUP`)로만 갈린다. `_migrate()` 가 모든 환경에서 레거시 데이터를 couple #1 로 멱등 승격한다.

**Tech Stack:** Python 3.11, FastAPI, SQLite(WAL), Jinja2, Alpine.js, itsdangerous, httpx, pytest. 테스트는 **반드시 `python3.11 -m pytest`**.

**작업 브랜치:** `beta` (이미 생성됨). **배포/서비스 재시작/시드 실행/CF 설정은 사장님 확인 후에만.**

---

## 파일 구조 (생성 C / 수정 M)

| 파일 | 책임 | C/M |
|---|---|---|
| `app/config.py` | `COUPLE_DB`·`SESSION_COOKIE`·`OPEN_SIGNUP` env 읽기 | M |
| `tests/conftest.py` | 임시 DB로 테스트 격리 + couple #1 시드 | C |
| `app/db.py` | 신규 테이블 + `couple_id` 마이그레이션 + couple #1 부트스트랩 + `kv_*(couple_id,…)` + `couple_members()` | M |
| `app/auth.py` | `open_signup`, `couple_of`, `partner_of`(DB기반), `require_couple` | M |
| `app/couples.py` | 커플/초대 핵심 로직(라우트와 분리, 테스트 용이) | C |
| `app/routes/couple_routes.py` | `/api/couple/*` (status/invite/invites/accept/decline/cancel/unlink) | C |
| `app/routes/auth_routes.py` | `/me` 닉네임을 커플 기반으로 | M |
| `app/routes/{calendar,places,bucket,photos,poke,chat,settings}_routes.py` | `require_couple` + 쿼리 스코프 | M |
| `app/special_events.py` | `ensure_special_events(couple_id,…)` 스코프 | M |
| `app/agent_tools.py` | `couple_id` 시그니처 주입 + 스코프 + `_broadcast(couple_id,…)` | M |
| `server.py` | `index()` 3분기, `_reminder_loop` 커플별, 라우터 등록 | M |
| `app/kakao_import.py` | 폴더 공유 링크 파싱/지오코딩(격리·테스트) | C |
| `templates/match.html` | 미매칭 화면(초대/수락만) | C |
| `templates/login.html` | 오픈가입 문구 | M |
| `templates/app.html` | 커플 해제 버튼 + 카카오 가져오기 UI | M |
| `scripts/seed_beta_db.py` | 운영 DB 파일 복제(읽기전용) | C |
| `deploy/couple-beta.service` | beta systemd(8801) | C |
| `deploy/BETA.md` | CF 대시보드 ingress/Access 해제 안내 | C |
| `tests/test_*.py` | 신규 테스트 6종 + 기존 갱신 | C/M |

---

## Phase 0 — 환경 분리 & 테스트 격리

### Task 0.1: config 가 DB 경로·쿠키명·오픈가입을 env 에서 읽기

**Files:** Modify `app/config.py`

- [ ] **Step 1: `app/config.py` 의 `class Settings` 본문 수정**

`db_path` 줄과 그 아래에 env 오버라이드를 추가하고, 두 신규 필드를 넣는다.

```python
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    uploads_dir: Path = BASE_DIR / "uploads" / "photos"
    db_path: Path = Path(os.getenv("COUPLE_DB") or (BASE_DIR / "data" / "couple.db"))

    session_cookie: str = os.getenv("SESSION_COOKIE", "couple_session")
    open_signup: bool = os.getenv("OPEN_SIGNUP", "").lower() in ("1", "true", "yes")

    allowed_emails: list[str] = _emails()
```

- [ ] **Step 2: 검증**

Run: `python3.11 -c "from app.config import settings; print(settings.db_path, settings.session_cookie, settings.open_signup)"`
Expected: `.../data/couple.db couple_session False`

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(config): COUPLE_DB / SESSION_COOKIE / OPEN_SIGNUP env 분리"
```

### Task 0.2: `SESSION_COOKIE` 상수를 settings 로 일원화

**Files:** Modify `app/auth.py`, `app/routes/auth_routes.py`

- [ ] **Step 1:** `app/auth.py` 의 `SESSION_COOKIE = "couple_session"` 를 교체

```python
SESSION_COOKIE = settings.session_cookie
```

- [ ] **Step 2:** `app/routes/auth_routes.py` 는 이미 `from ..auth import SESSION_COOKIE` 를 쓰므로 변경 없음. import 확인만.

- [ ] **Step 3: Commit**

```bash
git add app/auth.py
git commit -m "refactor(auth): 세션 쿠키명을 settings.session_cookie 로"
```

### Task 0.3: 테스트 격리용 conftest

**Files:** Create `tests/conftest.py`

격리 핵심: `server`/`app` import **이전에** `COUPLE_DB`·`OPEN_SIGNUP` 를 설정해야 한다. pytest 는 test 모듈보다 `conftest.py` 를 먼저 import 하므로 top-level 에서 env 를 박는다.

- [ ] **Step 1: 작성**

```python
"""테스트 격리: 임시 DB 로 운영 couple.db 를 절대 건드리지 않는다.
server/app import 전에 env 를 박아야 config·db 가 임시 경로를 집는다."""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="couple-test-"), "test.db")
os.environ["COUPLE_DB"] = _TMP_DB
os.environ["OPEN_SIGNUP"] = "1"

import pytest
from app.config import settings
from app import db as _db


@pytest.fixture(scope="session", autouse=True)
def _seed_couple_one():
    """allowed_emails 2명으로 couple #1 시드 — 기존 테스트(CF Access 헤더)가
    require_couple 게이트를 통과하도록."""
    a, b = (settings.allowed_emails + ["a@test", "b@test"])[:2]
    with _db.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                    "VALUES (1, ?, ?, '2026-01-01')", (a, b))
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, 1) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=1", (em,))
    yield
```

- [ ] **Step 2: 검증(기존 테스트가 여전히 통과)**

Run: `python3.11 -m pytest tests/test_calendar_multiday.py -q`
Expected: 통과(아직 require_couple 도입 전이므로 무해), DB 는 임시파일 사용.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: 임시 DB 격리 conftest + couple #1 시드"
```

---

## Phase 1 — DB 스키마 · 마이그레이션 · 부트스트랩

### Task 1.1: 신규 테이블 + couple_id 컬럼 + settings_kv 복합키 (TDD)

**Files:** Modify `app/db.py`; Test `tests/test_migration_seed.py`

- [ ] **Step 1: 실패 테스트 작성** `tests/test_migration_seed.py`

```python
"""스키마/마이그레이션: 신규 테이블·couple_id·settings_kv 복합키·couple #1 백필."""
from app import db


def _table_cols(table):
    with db.cursor() as cur:
        return {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}


def test_new_tables_exist():
    with db.cursor() as cur:
        names = {r["name"] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"couples", "couple_invites"} <= names


def test_couple_id_columns_added():
    for t in ("users", "events", "places", "bucket", "photos", "pokes", "chat_messages"):
        assert "couple_id" in _table_cols(t), t


def test_settings_kv_is_composite():
    cols = _table_cols("settings_kv")
    assert "couple_id" in cols and "key" in cols and "value" in cols
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_migration_seed.py -q`
Expected: FAIL (`couples` 없음 / `couple_id` 없음).

- [ ] **Step 3: `app/db.py` 수정 — 상단 import 에 datetime 추가**

```python
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from .config import settings
```

- [ ] **Step 4: `SCHEMA` 에 신규 테이블 추가 + settings_kv 를 복합키로 교체**

`settings_kv` 정의를 아래로 교체:

```sql
CREATE TABLE IF NOT EXISTS settings_kv (
    couple_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (couple_id, key)
);
```

`SCHEMA` 문자열 끝(마지막 `"""` 직전)에 추가:

```sql
CREATE TABLE IF NOT EXISTS couples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_a TEXT NOT NULL,
    member_b TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS couple_invites (
    id TEXT PRIMARY KEY,
    inviter_email TEXT NOT NULL,
    invitee_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    responded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invites_invitee ON couple_invites (invitee_email, status);
CREATE INDEX IF NOT EXISTS idx_invites_inviter ON couple_invites (inviter_email, status);
```

- [ ] **Step 5: `_migrate()` 를 아래로 교체**

```python
def _migrate() -> None:
    """멱등 마이그레이션: couple_id 컬럼·end_date·settings_kv 복합키 + couple #1 부트스트랩."""
    with cursor() as cur:
        # 1) couple_id 컬럼 (멱등)
        for table in ("users", "events", "places", "bucket", "photos", "pokes", "chat_messages"):
            cols = {r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            if "couple_id" not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN couple_id INTEGER")
        # events.end_date (기존 마이그레이션 유지)
        ev_cols = {r["name"] for r in cur.execute("PRAGMA table_info(events)").fetchall()}
        if "end_date" not in ev_cols:
            cur.execute("ALTER TABLE events ADD COLUMN end_date TEXT")
        # 2) settings_kv 단일PK → (couple_id,key) 복합키 재작성 (멱등)
        kv_cols = {r["name"] for r in cur.execute("PRAGMA table_info(settings_kv)").fetchall()}
        if "couple_id" not in kv_cols:
            cur.execute("ALTER TABLE settings_kv RENAME TO settings_kv_old")
            cur.execute(
                "CREATE TABLE settings_kv (couple_id INTEGER NOT NULL, key TEXT NOT NULL, "
                "value TEXT, PRIMARY KEY (couple_id, key))"
            )
            cur.execute("INSERT INTO settings_kv (couple_id, key, value) "
                        "SELECT 1, key, value FROM settings_kv_old")
            cur.execute("DROP TABLE settings_kv_old")
    _bootstrap_couple_one()
```

- [ ] **Step 6: 실행/통과**

Run: `python3.11 -m pytest tests/test_migration_seed.py::test_new_tables_exist tests/test_migration_seed.py::test_couple_id_columns_added tests/test_migration_seed.py::test_settings_kv_is_composite -q`
Expected: PASS (단, `_bootstrap_couple_one` 미정의로 import 에러면 Task 1.2 와 함께 통과 — 1.2 까지 한 커밋으로 묶어도 됨).

### Task 1.2: couple #1 부트스트랩 + 백필 (TDD)

**Files:** Modify `app/db.py`; Test `tests/test_migration_seed.py`

- [ ] **Step 1: 테스트 추가**

```python
def test_bootstrap_promotes_legacy_rows_to_couple_one():
    # 레거시 행(couple_id NULL) 삽입 → 부트스트랩이 1 로 승격
    with db.cursor() as cur:
        cur.execute("INSERT INTO bucket (id, title, created_at) VALUES ('lg1', 'legacy', '2026-01-01')")
    db._bootstrap_couple_one()
    with db.cursor() as cur:
        row = cur.execute("SELECT couple_id FROM bucket WHERE id='lg1'").fetchone()
    assert row["couple_id"] == 1
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_migration_seed.py::test_bootstrap_promotes_legacy_rows_to_couple_one -q`
Expected: FAIL (`_bootstrap_couple_one` 없음).

- [ ] **Step 3: `app/db.py` 에 `_bootstrap_couple_one` 추가(`_migrate` 위)**

```python
def _bootstrap_couple_one() -> None:
    """레거시 단일커플 데이터를 couple #1 로 멱등 승격.
    - couples 가 비었고 레거시(couple_id NULL) 데이터가 있으면 ALLOWED_EMAILS 2명으로 #1 생성.
    - couple #1 이 있으면 NULL 인 모든 데이터/유저/kv 를 1 로 백필."""
    emails = settings.allowed_emails
    with cursor() as cur:
        has_one = cur.execute("SELECT 1 FROM couples WHERE id=1").fetchone()
        legacy = cur.execute(
            "SELECT 1 FROM events WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM places WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM bucket WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM photos WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM pokes WHERE couple_id IS NULL "
            "UNION SELECT 1 FROM chat_messages WHERE couple_id IS NULL LIMIT 1"
        ).fetchone()
        if not has_one and legacy and len(emails) >= 2:
            cur.execute("INSERT INTO couples (id, member_a, member_b, created_at) "
                        "VALUES (1, ?, ?, ?)",
                        (emails[0], emails[1], datetime.now().isoformat(timespec="seconds")))
            has_one = True
        if has_one:
            for table in ("events", "places", "bucket", "photos", "pokes", "chat_messages"):
                cur.execute(f"UPDATE {table} SET couple_id=1 WHERE couple_id IS NULL")
            for em in emails[:2]:
                cur.execute("INSERT INTO users (email, couple_id) VALUES (?, 1) "
                            "ON CONFLICT(email) DO UPDATE SET couple_id=1", (em,))
```

- [ ] **Step 4: 실행/통과**

Run: `python3.11 -m pytest tests/test_migration_seed.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_migration_seed.py
git commit -m "feat(db): couples/couple_invites + couple_id 마이그레이션 + #1 부트스트랩"
```

### Task 1.3: `kv_get/kv_set` 커플 스코프 + `couple_members` 헬퍼 (TDD)

**Files:** Modify `app/db.py`; Test `tests/test_migration_seed.py`

- [ ] **Step 1: 테스트 추가**

```python
def test_kv_is_per_couple():
    db.kv_set(1, "theme", "rosy")
    db.kv_set(2, "theme", "mint")
    assert db.kv_get(1, "theme") == "rosy"
    assert db.kv_get(2, "theme") == "mint"
    assert db.kv_get(3, "theme", "default") == "default"


def test_couple_members_returns_both():
    with db.cursor() as cur:
        cur.execute("INSERT OR IGNORE INTO couples (id, member_a, member_b, created_at) "
                    "VALUES (9, 'x@t', 'y@t', '2026-01-01')")
    assert set(db.couple_members(9)) == {"x@t", "y@t"}
    assert db.couple_members(999) == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_migration_seed.py::test_kv_is_per_couple -q`
Expected: FAIL (`kv_get` 시그니처 불일치).

- [ ] **Step 3: `app/db.py` 의 `kv_get`/`kv_set` 교체 + `couple_members` 추가**

```python
def kv_get(couple_id: int, key: str, default: str | None = None) -> str | None:
    with cursor() as cur:
        row = cur.execute("SELECT value FROM settings_kv WHERE couple_id=? AND key=?",
                          (couple_id, key)).fetchone()
        return row["value"] if row else default


def kv_set(couple_id: int, key: str, value: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO settings_kv (couple_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(couple_id, key) DO UPDATE SET value=excluded.value",
            (couple_id, key, value),
        )


def couple_members(couple_id: int) -> list[str]:
    with cursor() as cur:
        row = cur.execute("SELECT member_a, member_b FROM couples WHERE id=?",
                          (couple_id,)).fetchone()
    return [row["member_a"], row["member_b"]] if row else []
```

- [ ] **Step 4: 실행/통과 + Commit**

Run: `python3.11 -m pytest tests/test_migration_seed.py -q` → PASS

```bash
git add app/db.py tests/test_migration_seed.py
git commit -m "feat(db): kv_get/kv_set 커플 스코프 + couple_members 헬퍼"
```

> ⚠️ 이 시점에서 `kv_get/kv_set` 의 기존 호출부(settings_routes/special_events/chat_routes/agent_tools/server)가 깨진다. Phase 3·5·6 에서 호출부를 함께 고친다. 중간 단계에서 `python3.11 -m pytest` 전체가 빨갈 수 있으니, 아래 순서대로 진행하면 Phase 6 끝에서 전부 초록이 된다.

---

## Phase 2 — 인증 계층

### Task 2.1: 오픈가입 + 커플 조회 헬퍼 (TDD)

**Files:** Modify `app/auth.py`; Test `tests/test_auth_couple.py`

- [ ] **Step 1: 테스트 작성** `tests/test_auth_couple.py`

```python
from app import auth
from app.config import settings


def test_open_signup_allows_any_email(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", True)
    assert auth.is_allowed("stranger@example.com") is True


def test_closed_signup_keeps_whitelist(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", False)
    assert auth.is_allowed("stranger@example.com") is False
    assert auth.is_allowed(settings.allowed_emails[0]) is True


def test_couple_of_and_partner(monkeypatch):
    # conftest 가 couple #1 을 allowed_emails[0],[1] 로 시드함
    a, b = settings.allowed_emails[0], settings.allowed_emails[1]
    assert auth.couple_of(a) == 1
    assert auth.partner_of(a) == b
    assert auth.couple_of("nobody@test") is None
    assert auth.partner_of("nobody@test") is None
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_auth_couple.py -q`
Expected: FAIL.

- [ ] **Step 3: `app/auth.py` 수정**

`is_allowed` 교체:

```python
def is_allowed(email: str) -> bool:
    email = email.lower().strip()
    if not email or "@" not in email:
        return False
    if settings.open_signup:
        return True
    return email in settings.allowed_emails
```

`partner_of` 교체 + `couple_of`/`require_couple` 추가(파일 끝):

```python
def couple_of(email: str) -> int | None:
    from .db import cursor
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
```

- [ ] **Step 4: 실행/통과 + Commit**

Run: `python3.11 -m pytest tests/test_auth_couple.py -q` → PASS

```bash
git add app/auth.py tests/test_auth_couple.py
git commit -m "feat(auth): open_signup + couple_of/partner_of(DB) + require_couple"
```

> 참고: `app/auth.py::consume_code` 의 `INSERT INTO users (email) ... ON CONFLICT DO NOTHING` 는 그대로 둔다(가입 시 user 행 생성, couple_id 는 NULL = 미매칭). 변경 불필요.

---

## Phase 3 — 커플 핵심 로직 & 라우트

### Task 3.1: 커플/초대 도메인 로직 (TDD)

**Files:** Create `app/couples.py`; Test `tests/test_couple_invite.py`

라우트와 분리해 순수 함수로 테스트한다. 모든 함수는 `(ok: bool, data|error)` 대신 성공 시 dict 반환·실패 시 `ValueError(detail)` 발생 방식으로 단순화한다.

- [ ] **Step 1: 테스트 작성** `tests/test_couple_invite.py`

```python
import pytest
from app import couples, auth
from app import db


def _fresh(email):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (email,))


def test_invite_creates_pending():
    _fresh("u1@t"); _fresh("u2@t")
    inv = couples.create_invite("u1@t", "u2@t")
    assert inv["status"] == "pending"
    incoming = couples.list_invites("u2@t")["incoming"]
    assert any(i["inviter_email"] == "u1@t" for i in incoming)


def test_cannot_invite_self():
    _fresh("u3@t")
    with pytest.raises(ValueError):
        couples.create_invite("u3@t", "u3@t")


def test_accept_forms_couple_and_clears_others():
    _fresh("a1@t"); _fresh("a2@t"); _fresh("a3@t")
    couples.create_invite("a3@t", "a2@t")          # 다른 pending
    inv = couples.create_invite("a1@t", "a2@t")
    couples.accept_invite("a2@t", inv["id"])
    assert auth.partner_of("a1@t") == "a2@t"
    # a2 가 매칭되면 a3 의 pending 은 canceled
    assert couples.list_invites("a3@t")["outgoing"] == []


def test_cannot_invite_already_matched():
    _fresh("b1@t"); _fresh("b2@t"); _fresh("b3@t")
    inv = couples.create_invite("b1@t", "b2@t")
    couples.accept_invite("b2@t", inv["id"])
    with pytest.raises(ValueError):
        couples.create_invite("b3@t", "b1@t")      # b1 이미 매칭


def test_preinvite_unregistered_email_ok():
    _fresh("c1@t")
    inv = couples.create_invite("c1@t", "newcomer@t")   # 미가입 이메일 허용(b)
    assert inv["invitee_email"] == "newcomer@t"


def test_decline_and_cancel():
    _fresh("d1@t"); _fresh("d2@t")
    inv = couples.create_invite("d1@t", "d2@t")
    couples.decline_invite("d2@t", inv["id"])
    assert couples.list_invites("d2@t")["incoming"] == []
    inv2 = couples.create_invite("d1@t", "d2@t")
    couples.cancel_invite("d1@t", inv2["id"])
    assert couples.list_invites("d1@t")["outgoing"] == []


def test_unlink_dissolves_couple():
    _fresh("e1@t"); _fresh("e2@t")
    inv = couples.create_invite("e1@t", "e2@t")
    couples.accept_invite("e2@t", inv["id"])
    couples.unlink(auth.couple_of("e1@t"))
    assert auth.couple_of("e1@t") is None
    assert auth.couple_of("e2@t") is None
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_couple_invite.py -q`
Expected: FAIL (`app.couples` 없음).

- [ ] **Step 3: `app/couples.py` 작성**

```python
"""커플 매칭/초대 도메인 로직 (라우트와 분리, 순수 테스트 가능).
실패는 ValueError(detail) 로 던지고, 라우트가 400 으로 변환한다."""
import secrets
from datetime import datetime, timezone

from .auth import couple_of
from .db import cursor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(email: str) -> str:
    return (email or "").lower().strip()


def create_invite(inviter: str, invitee: str) -> dict:
    inviter, invitee = _norm(inviter), _norm(invitee)
    if not invitee or "@" not in invitee:
        raise ValueError("bad_email")
    if inviter == invitee:
        raise ValueError("cannot_invite_self")
    if couple_of(inviter):
        raise ValueError("already_matched")
    if couple_of(invitee):
        raise ValueError("invitee_already_matched")
    with cursor() as cur:
        dup = cur.execute(
            "SELECT 1 FROM couple_invites WHERE inviter_email=? AND invitee_email=? "
            "AND status='pending'", (inviter, invitee)).fetchone()
        if dup:
            raise ValueError("duplicate_invite")
        iid = secrets.token_urlsafe(8)
        cur.execute(
            "INSERT INTO couple_invites (id, inviter_email, invitee_email, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)", (iid, inviter, invitee, _now()))
    return {"id": iid, "inviter_email": inviter, "invitee_email": invitee, "status": "pending"}


def list_invites(email: str) -> dict:
    email = _norm(email)
    with cursor() as cur:
        incoming = [dict(r) for r in cur.execute(
            "SELECT * FROM couple_invites WHERE invitee_email=? AND status='pending' "
            "ORDER BY created_at DESC", (email,)).fetchall()]
        outgoing = [dict(r) for r in cur.execute(
            "SELECT * FROM couple_invites WHERE inviter_email=? AND status='pending' "
            "ORDER BY created_at DESC", (email,)).fetchall()]
    return {"incoming": incoming, "outgoing": outgoing}


def _get_invite(iid: str) -> dict | None:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM couple_invites WHERE id=?", (iid,)).fetchone()
    return dict(row) if row else None


def accept_invite(invitee: str, iid: str) -> dict:
    invitee = _norm(invitee)
    inv = _get_invite(iid)
    if not inv or inv["status"] != "pending" or inv["invitee_email"] != invitee:
        raise ValueError("invite_not_found")
    inviter = inv["inviter_email"]
    if couple_of(inviter):
        raise ValueError("inviter_already_matched")
    if couple_of(invitee):
        raise ValueError("already_matched")
    with cursor() as cur:
        cur.execute("INSERT INTO couples (member_a, member_b, created_at) VALUES (?, ?, ?)",
                    (inviter, invitee, _now()))
        cid = cur.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for em in (inviter, invitee):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, ?) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=excluded.couple_id", (em, cid))
        cur.execute("UPDATE couple_invites SET status='accepted', responded_at=? WHERE id=?",
                    (_now(), iid))
        # 두 사람과 얽힌 다른 pending 전부 취소
        cur.execute(
            "UPDATE couple_invites SET status='canceled', responded_at=? "
            "WHERE status='pending' AND id!=? AND "
            "(inviter_email IN (?,?) OR invitee_email IN (?,?))",
            (_now(), iid, inviter, invitee, inviter, invitee))
    return {"couple_id": cid, "partner": inviter}


def decline_invite(invitee: str, iid: str) -> None:
    invitee = _norm(invitee)
    inv = _get_invite(iid)
    if not inv or inv["invitee_email"] != invitee:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='declined', responded_at=? WHERE id=?",
                    (_now(), iid))


def cancel_invite(inviter: str, iid: str) -> None:
    inviter = _norm(inviter)
    inv = _get_invite(iid)
    if not inv or inv["inviter_email"] != inviter:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='canceled', responded_at=? WHERE id=?",
                    (_now(), iid))


def unlink(couple_id: int) -> list[str]:
    """커플 해제: couples 행 삭제 + 양쪽 couple_id=NULL. 데이터는 옛 couple_id 로 보존(접근 불가).
    반환: 해제된 멤버 이메일 목록(알림용)."""
    from .db import couple_members
    members = couple_members(couple_id)
    with cursor() as cur:
        cur.execute("UPDATE users SET couple_id=NULL WHERE couple_id=?", (couple_id,))
        cur.execute("DELETE FROM couples WHERE id=?", (couple_id,))
    return members
```

- [ ] **Step 4: 실행/통과 + Commit**

Run: `python3.11 -m pytest tests/test_couple_invite.py -q` → PASS (8 tests)

```bash
git add app/couples.py tests/test_couple_invite.py
git commit -m "feat(couples): 초대/수락/거절/취소/해제 도메인 로직 + 가드"
```

### Task 3.2: 커플 라우트 (TDD)

**Files:** Create `app/routes/couple_routes.py`; Modify `server.py`(라우터 등록); Test `tests/test_couple_routes.py`

- [ ] **Step 1: 테스트 작성** `tests/test_couple_routes.py`

```python
from fastapi.testclient import TestClient
from server import app
from app import db

client = TestClient(app)


def _login(email):
    return {"Cf-Access-Authenticated-User-Email": email}   # open_signup=1 → 누구나 인증


def _unmatch(email):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (email,))


def test_status_unmatched():
    _unmatch("r1@t")
    r = client.get("/api/couple/status", headers=_login("r1@t"))
    assert r.json()["matched"] is False


def test_invite_accept_flow():
    _unmatch("r2@t"); _unmatch("r3@t")
    inv = client.post("/api/couple/invite", json={"email": "r3@t"}, headers=_login("r2@t"))
    assert inv.status_code == 200
    iid = inv.json()["id"]
    # r3 받은 초대 확인
    inb = client.get("/api/couple/invites", headers=_login("r3@t")).json()
    assert any(i["id"] == iid for i in inb["incoming"])
    # 수락
    acc = client.post(f"/api/couple/invites/{iid}/accept", headers=_login("r3@t"))
    assert acc.status_code == 200
    st = client.get("/api/couple/status", headers=_login("r2@t")).json()
    assert st["matched"] is True and st["partner_email"] == "r3@t"


def test_invite_self_400():
    _unmatch("r4@t")
    r = client.post("/api/couple/invite", json={"email": "r4@t"}, headers=_login("r4@t"))
    assert r.status_code == 400


def test_unlink():
    _unmatch("r5@t"); _unmatch("r6@t")
    iid = client.post("/api/couple/invite", json={"email": "r6@t"},
                      headers=_login("r5@t")).json()["id"]
    client.post(f"/api/couple/invites/{iid}/accept", headers=_login("r6@t"))
    r = client.post("/api/couple/unlink", headers=_login("r5@t"))
    assert r.status_code == 200
    assert client.get("/api/couple/status", headers=_login("r5@t")).json()["matched"] is False
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_couple_routes.py -q`
Expected: FAIL (404 — 라우트 없음).

- [ ] **Step 3: `app/routes/couple_routes.py` 작성**

```python
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr

from .. import couples
from ..auth import require_user, couple_of, partner_of
from ..db import kv_get, couple_members
from ..realtime import hub

router = APIRouter(prefix="/api/couple", tags=["couple"])


class InviteIn(BaseModel):
    email: EmailStr


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
    return {
        "matched": True,
        "couple_id": cid,
        "partner_email": partner,
        "partner_nickname": kv_get(cid, "nickname_b", "") if partner else "",
    }


@router.post("/invite")
async def invite(body: InviteIn, request: Request):
    email = require_user(request)
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
```

- [ ] **Step 4: `server.py` 라우터 등록**

import 블록(`from app.routes import (`)에 `couple_routes` 추가하고, 등록부에 한 줄 추가:

```python
from app.routes import (
    auth_routes,
    couple_routes,
    settings_routes,
    photos_routes,
    calendar_routes,
    bucket_routes,
    places_routes,
    poke_routes,
    chat_routes,
)
```

```python
app.include_router(auth_routes.router)
app.include_router(couple_routes.router)
app.include_router(settings_routes.router)
```

- [ ] **Step 5: 실행/통과 + Commit**

Run: `python3.11 -m pytest tests/test_couple_routes.py -q` → PASS

```bash
git add app/routes/couple_routes.py server.py tests/test_couple_routes.py
git commit -m "feat(routes): /api/couple status/invite/accept/decline/cancel/unlink"
```

---

## Phase 4 — 기존 라우트 테넌트 스코핑

> 패턴(모든 파일 공통): `require_user(request)` → `email, cid = require_couple(request)`; SELECT/UPDATE/DELETE 에 `couple_id` 조건; INSERT 에 `couple_id` 값. `partner_of(user)` 는 그대로 동작(이제 DB 기반).

### Task 4.1: 격리 회귀 테스트 (먼저 작성, RED 유지)

**Files:** Test `tests/test_tenant_isolation.py`

- [ ] **Step 1: 작성**

```python
"""두 커플의 데이터가 서로 안 보여야 한다."""
from fastapi.testclient import TestClient
from server import app
from app import db

client = TestClient(app)


def _match(a, b):
    from app import couples
    with db.cursor() as cur:
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (em,))
    inv = couples.create_invite(a, b)
    couples.accept_invite(b, inv["id"])


def _h(email):
    return {"Cf-Access-Authenticated-User-Email": email}


def test_events_isolated_between_couples():
    _match("iso_a1@t", "iso_a2@t")
    _match("iso_b1@t", "iso_b2@t")
    client.post("/api/events", json={"title": "A커플 일정", "due": "2026-09-01"}, headers=_h("iso_a1@t"))
    b_events = client.get("/api/events", headers=_h("iso_b1@t")).json()
    assert all(e["title"] != "A커플 일정" for e in b_events)


def test_places_isolated_between_couples():
    _match("iso_c1@t", "iso_c2@t")
    _match("iso_d1@t", "iso_d2@t")
    client.post("/api/places", json={"name": "C커플 장소", "lat": 37.5, "lng": 127.0},
                headers=_h("iso_c1@t"))
    d_places = client.get("/api/places", headers=_h("iso_d1@t")).json()
    assert all(p["name"] != "C커플 장소" for p in d_places)


def test_unmatched_data_route_409():
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES ('lone@t', NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL")
    assert client.get("/api/events", headers=_h("lone@t")).status_code == 409
```

- [ ] **Step 2: 실패 확인** (스코핑 전이라 격리 깨짐)

Run: `python3.11 -m pytest tests/test_tenant_isolation.py -q`
Expected: FAIL.

### Task 4.2: calendar_routes 스코핑

**Files:** Modify `app/routes/calendar_routes.py`

- [ ] **Step 1: import 교체**

```python
from ..auth import require_couple, partner_of
```

- [ ] **Step 2: `list_events` 교체**

```python
@router.get("")
def list_events(request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM events WHERE couple_id=? "
            "ORDER BY due IS NULL, due ASC, time IS NULL, time ASC, created_at DESC",
            (cid,)).fetchall()]
    _auto_complete_past(rows)
    return rows
```

- [ ] **Step 3: `create` 의 첫 줄·INSERT 교체**

`user = require_user(request)` → `user, cid = require_couple(request)`
INSERT 의 컬럼/값에 `couple_id` 추가:

```python
        cur.execute(
            """INSERT INTO events
               (id, title, due, end_date, time, note, color, source, done, reminder_minutes,
                owner_email, couple_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (
                eid, body.title.strip(), body.due, end_date, body.time, body.note,
                body.color, body.source, body.reminder_minutes, user, cid,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
            ),
        )
```

- [ ] **Step 4: `patch`·`delete` 교체**

`patch`: 첫 줄 `user, cid = require_couple(request)`; UPDATE 에 couple 가드:

```python
    args.append(eid)
    args.append(cid)
    with cursor() as cur:
        cur.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
```

`delete`:

```python
@router.delete("/{eid}")
def delete(eid: str, request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE id=? AND couple_id=?", (eid, cid))
    return {"ok": True}
```

`_auto_complete_past` 의 내부 `UPDATE events SET done=1 WHERE id=?` 는 id 가 이미 그 커플 행이므로 그대로 둬도 안전(잘못된 커플 행을 만지지 않음).

- [ ] **Step 5: 부분 통과 확인**

Run: `python3.11 -m pytest tests/test_tenant_isolation.py::test_events_isolated_between_couples tests/test_tenant_isolation.py::test_unmatched_data_route_409 -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/routes/calendar_routes.py
git commit -m "feat(events): couple_id 테넌트 스코핑"
```

### Task 4.3: places_routes 스코핑

**Files:** Modify `app/routes/places_routes.py`

- [ ] **Step 1:** import → `from ..auth import require_couple, partner_of`
- [ ] **Step 2: `list_places`**

```python
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
```

- [ ] **Step 3: `create`** — 첫 줄 `user, cid = require_couple(request)`, INSERT 에 couple_id:

```python
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
```

- [ ] **Step 4: `patch`·`delete`** — `require_couple` + `AND couple_id=?`:

```python
@router.patch("/{pid}")
def patch(pid: str, body: PlacePatch, request: Request):
    _email, cid = require_couple(request)
    ...
    args.append(pid); args.append(cid)
    with cursor() as cur:
        cur.execute(f"UPDATE places SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    return {"ok": True}


@router.delete("/{pid}")
def delete(pid: str, request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM places WHERE id=? AND couple_id=?", (pid, cid))
    return {"ok": True}
```

- [ ] **Step 5: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_tenant_isolation.py -q` → PASS (3 tests)

```bash
git add app/routes/places_routes.py
git commit -m "feat(places): couple_id 테넌트 스코핑"
```

### Task 4.4: bucket_routes 스코핑

**Files:** Modify `app/routes/bucket_routes.py`; Test 추가 `tests/test_tenant_isolation.py`

- [ ] **Step 1: 격리 테스트 한 줄 추가**

```python
def test_bucket_isolated():
    _match("iso_e1@t", "iso_e2@t"); _match("iso_f1@t", "iso_f2@t")
    client.post("/api/bucket", json={"title": "E버킷"}, headers=_h("iso_e1@t"))
    f = client.get("/api/bucket", headers=_h("iso_f1@t")).json()
    assert all(b["title"] != "E버킷" for b in f)
```

- [ ] **Step 2:** import → `require_couple`; `list_items`:

```python
@router.get("")
def list_items(request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(
            "SELECT * FROM bucket WHERE couple_id=? ORDER BY done ASC, priority DESC, created_at DESC",
            (cid,)).fetchall()]
    return rows
```

- [ ] **Step 3:** `create` — `user, cid = require_couple(request)`, INSERT:

```python
        cur.execute(
            """INSERT INTO bucket
               (id, title, description, icon, target_date, priority, done,
                created_at, owner_email, couple_id)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (
                bid, body.title.strip(), body.description,
                body.icon or "💖", body.target_date, body.priority,
                datetime.now().isoformat(timespec="seconds"), user, cid,
            ),
        )
```

- [ ] **Step 4:** `patch`·`delete` — `require_couple` + `AND couple_id=?` (events 패턴 동일: `args.append(bid); args.append(cid)` 후 `WHERE id=? AND couple_id=?`; delete 는 `(bid, cid)`).

- [ ] **Step 5: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_tenant_isolation.py -q` → PASS

```bash
git add app/routes/bucket_routes.py tests/test_tenant_isolation.py
git commit -m "feat(bucket): couple_id 테넌트 스코핑"
```

### Task 4.5: photos_routes 스코핑

**Files:** Modify `app/routes/photos_routes.py`

> photos 는 미독본. 먼저 `Read app/routes/photos_routes.py` 로 정확한 라인 확인 후 동일 패턴 적용:
> - 모든 핸들러 `require_user` → `require_couple`.
> - `SELECT ... FROM photos WHERE 1=1` 에 `AND couple_id=?` 추가(목록/지도/단건).
> - INSERT(업로드)에 `couple_id` 컬럼/값 추가.
> - UPDATE/DELETE(단건·일괄)에 `AND couple_id=?` (일괄은 `id IN (...) AND couple_id=?`).
> - place 집계 쿼리(`SELECT ... FROM photos WHERE place_name ...`)에도 `AND couple_id=?`.

- [ ] **Step 1:** 위 패턴대로 수정.
- [ ] **Step 2:** 격리 테스트 추가(사진 업로드는 멀티파트라, 간단히 DB 직접 삽입으로 검증):

```python
def test_photos_isolated():
    _match("iso_g1@t", "iso_g2@t"); _match("iso_h1@t", "iso_h2@t")
    gcid = __import__("app.auth", fromlist=["couple_of"]).couple_of("iso_g1@t")
    with db.cursor() as cur:
        cur.execute("INSERT INTO photos (id, owner_email, filename, uploaded_at, couple_id) "
                    "VALUES ('ph1', 'iso_g1@t', 'x.jpg', '2026-01-01', ?)", (gcid,))
    h = client.get("/api/photos", headers=_h("iso_h1@t")).json()
    assert all(p.get("id") != "ph1" for p in h)
```

- [ ] **Step 3: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_tenant_isolation.py::test_photos_isolated -q` → PASS

```bash
git add app/routes/photos_routes.py tests/test_tenant_isolation.py
git commit -m "feat(photos): couple_id 테넌트 스코핑"
```

### Task 4.6: poke_routes 스코핑

**Files:** Modify `app/routes/poke_routes.py`

> pokes 는 `from_email`/`to_email` 로 이미 두 사람 사이로 묶이지만, 멀티커플에서 동명 이메일 충돌은 없으므로 기능상 안전하다. 다만 일관성·방어를 위해 couple_id 를 채우고 조회에 추가한다.

- [ ] **Step 1:** import → `from ..auth import partner_of, require_couple`
- [ ] **Step 2:** `list_pokes`·`unread_count`·`mark_seen`·`clear` 의 `user = require_user(request)` → `user, cid = require_couple(request)`; 각 쿼리에 `AND couple_id=?` 추가(예: `WHERE (to_email=? OR from_email=?) AND couple_id=?`).
- [ ] **Step 3:** `send` — `user, cid = require_couple(request)`; INSERT 에 `couple_id`:

```python
        cur.execute(
            "INSERT INTO pokes (id, from_email, to_email, emoji, message, seen, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (pid, user, partner, body.emoji, body.message, created, cid),
        )
```

- [ ] **Step 4:** 격리 테스트 추가 + 통과 + Commit

```python
def test_pokes_isolated():
    _match("iso_i1@t", "iso_i2@t")
    client.post("/api/pokes", json={"emoji": "💗"}, headers=_h("iso_i1@t"))
    # 다른 커플은 못 봄
    _match("iso_j1@t", "iso_j2@t")
    assert client.get("/api/pokes", headers=_h("iso_j1@t")).json() == []
```

```bash
git add app/routes/poke_routes.py tests/test_tenant_isolation.py
git commit -m "feat(pokes): couple_id 스코핑"
```

### Task 4.7: chat_routes 스코핑 (session_id 를 커플별로)

**Files:** Modify `app/routes/chat_routes.py`

- [ ] **Step 1:** import → `from ..auth import require_couple`; `from ..db import cursor, kv_get`(유지)
- [ ] **Step 2:** `_load_history`/`_save_msg` 에 `couple_id` 인자 추가, 쿼리 스코프:

```python
def _load_history(couple_id: int, session_id: str, limit: int = 12) -> list[dict]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT role, content, user_email FROM chat_messages "
            "WHERE couple_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
            (couple_id, session_id, limit)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def _save_msg(couple_id: int, session_id: str, role: str, content: str, email: str | None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (id, role, content, session_id, user_email, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (secrets.token_urlsafe(8), role, content, session_id, email,
             datetime.now().isoformat(timespec="seconds"), couple_id))
```

- [ ] **Step 3:** `_greeting(user_email)` 의 kv 호출을 couple 기반으로 — 시그니처에 couple_id 추가:

```python
def _greeting(couple_id: int, user_email: str) -> str:
    a = kv_get(couple_id, "nickname_a", cfg.nickname_a)
    b = kv_get(couple_id, "nickname_b", cfg.nickname_b)
    from ..db import couple_members
    members = couple_members(couple_id)
    target = a if members and user_email == members[0] else b
    hour = datetime.now(KST).hour
    ...
    emoji = MASCOT_EMOJI.get(kv_get(couple_id, "mascot", "bunny"), "🐰")
    ...
```

- [ ] **Step 4:** 라우트 핸들러 4개 교체:

```python
@router.get("/greeting")
def greeting(request: Request):
    email, cid = require_couple(request)
    return {"text": _greeting(cid, email)}


@router.get("/history")
def history(request: Request, session_id: str = "default"):
    _email, cid = require_couple(request)
    return _load_history(cid, session_id, limit=80)


@router.post("/send")
async def send(body: ChatIn, request: Request):
    email, cid = require_couple(request)
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="empty")
    sid = (body.session_id or "default")[:64]
    _save_msg(cid, sid, "user", msg, email)
    hist = _load_history(cid, sid, limit=12)[:-1]
    reply, tools = await _ai_turn(email, cid, hist, msg)
    _save_msg(cid, sid, "assistant", reply, None)
    return {"reply": reply, "tools_used": tools}


@router.delete("/history")
def clear(request: Request, session_id: str = "default"):
    _email, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM chat_messages WHERE couple_id=? AND session_id=?", (cid, session_id))
    return {"ok": True}
```

- [ ] **Step 5:** `_ai_turn` 시그니처에 `couple_id` 추가하고 `execute_tool(... , couple_id)` 로 전달:

```python
async def _ai_turn(user_email: str, couple_id: int, history: list[dict], message: str) -> tuple[str, list[str]]:
    ...
            result = await execute_tool(name, args, user_email, couple_id)
```

(Task 4.8 에서 `execute_tool` 가 couple_id 를 받도록 바꾼다.)

- [ ] **Step 6: Commit** (agent_tools 변경 후 함께 검증)

```bash
git add app/routes/chat_routes.py
git commit -m "feat(chat): couple_id 스코핑 + 커플별 history/greeting"
```

### Task 4.8: agent_tools 스코핑 (couple_id 시그니처 주입)

**Files:** Modify `app/agent_tools.py`; Test `tests/test_agent_tools_scope.py`

- [ ] **Step 1: 테스트 작성** `tests/test_agent_tools_scope.py`

```python
import asyncio
from app import agent_tools, db


def test_add_and_list_place_scoped_by_couple():
    async def go():
        await agent_tools.add_place("z1@t", name="Z장소", kind="wishlist",
                                    lat=37.5, lng=127.0, couple_id=77)
        mine = await agent_tools.list_places(couple_id=77)
        other = await agent_tools.list_places(couple_id=88)
        return mine, other
    mine, other = asyncio.run(go())
    assert any(p["name"] == "Z장소" for p in mine["items"])
    assert all(p["name"] != "Z장소" for p in other["items"])
```

- [ ] **Step 2: 실패 확인**

Run: `python3.11 -m pytest tests/test_agent_tools_scope.py -q`
Expected: FAIL (`couple_id` 미지원).

- [ ] **Step 3: `execute_tool` 가 couple_id 도 주입하도록**

```python
async def execute_tool(name: str, args: dict, user_email: str, couple_id: int) -> dict:
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    args = {k: v for k, v in (args or {}).items() if k not in ("user_email", "couple_id")}
    try:
        params = inspect.signature(fn).parameters
        if "user_email" in params:
            args["user_email"] = user_email
        if "couple_id" in params:
            args["couple_id"] = couple_id
        return await fn(**args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 4: `_broadcast` 를 커플 멤버로**

```python
async def _broadcast(couple_id: int, kind: str, **extra) -> None:
    from .db import couple_members
    payload = {"kind": kind, **extra}
    for em in couple_members(couple_id):
        await hub.send(em, payload)
```

- [ ] **Step 5: 각 도구에 `couple_id` 추가 + 쿼리 스코프**

데이터 도구 전부(`add_place/list_places/update_place/delete_place/add_event/list_events/update_event/delete_event/add_bucket/list_bucket/update_bucket/delete_bucket/list_photos/update_photo/delete_photo/send_poke/get_settings/update_settings`)에 `couple_id: int` 파라미터를 추가하고:
- INSERT → `couple_id` 컬럼/값 추가.
- SELECT/UPDATE/DELETE → `WHERE ... couple_id=?` 추가(목록은 `WHERE couple_id=?` 로 `WHERE 1=1` 대체, 단건 update/delete 는 `... AND couple_id=?`).
- `_broadcast(...)` 첫 인자로 `couple_id`.
- `get_settings/update_settings` 의 `kv_get/kv_set` 에 `couple_id` 전달.
- `send_poke` 의 INSERT 에 `couple_id`, partner 조회는 `partner_of(user_email)` 유지.

대표 예시(add_place):

```python
async def add_place(user_email: str, couple_id: int, name: str, kind: str, lat: float, lng: float,
                    address: str = "", category: str = "", memo: str = "", **_) -> dict:
    if kind not in ("visited", "wishlist", "revisit"):
        return {"error": "kind must be 'visited', 'wishlist', or 'revisit'"}
    pid = _now_ms()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO places (id, name, address, lat, lng, kind, category, memo, "
            "created_at, owner_email, couple_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, name, address, float(lat), float(lng), kind, category, memo,
             _now_iso(), user_email, couple_id))
    await _broadcast(couple_id, "place_added", name=name, place_kind=kind, by=user_email)
    return {"id": pid, "name": name, "kind": kind, "ok": True}


async def list_places(couple_id: int, kind: str = "", query: str = "", **_) -> dict:
    sql = "SELECT id, name, address, lat, lng, kind, category, memo FROM places WHERE couple_id=?"
    args: list = [couple_id]
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if query:
        sql += " AND (name LIKE ? OR address LIKE ? OR memo LIKE ?)"; args.extend([f"%{query}%"] * 3)
    sql += " ORDER BY created_at DESC LIMIT 50"
    with cursor() as cur:
        return {"items": [dict(r) for r in cur.execute(sql, args).fetchall()]}
```

나머지 도구도 동일 규칙으로 변환(읽기 도구는 `couple_id` 만, 쓰기 도구는 `user_email, couple_id` 둘 다). **`TOOL_DECLARATIONS`(Gemini 스키마)는 변경하지 않는다** — couple_id 는 서버가 주입하지 모델이 넘기지 않는다.

- [ ] **Step 6: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_agent_tools_scope.py -q` → PASS

```bash
git add app/agent_tools.py tests/test_agent_tools_scope.py
git commit -m "feat(agent_tools): couple_id 주입·스코프 + _broadcast 커플 멤버"
```

---

## Phase 5 — settings/special_events/index/reminder

### Task 5.1: settings_routes 커플 스코프

**Files:** Modify `app/routes/settings_routes.py`

- [ ] **Step 1:** import → `from ..auth import require_couple, require_user`
- [ ] **Step 2:** `_current(cid)` 로 시그니처 변경, 모든 `kv_get(key, default)` → `kv_get(cid, key, default)`. `allowed_emails` 노출은 제거(멀티커플에서 의미 없음):

```python
def _current(cid: int) -> dict:
    return {
        "anniversary_date": kv_get(cid, "anniversary_date", cfg.anniversary_date),
        "nickname_a": kv_get(cid, "nickname_a", cfg.nickname_a),
        "nickname_b": kv_get(cid, "nickname_b", cfg.nickname_b),
        "birthday_a": kv_get(cid, "birthday_a", cfg.birthday_a),
        "birthday_b": kv_get(cid, "birthday_b", cfg.birthday_b),
        "theme": kv_get(cid, "theme", "rosy"),
        "mascot": kv_get(cid, "mascot", "bunny"),
        "kakao_js_key": cfg.kakao_js_key,
    }
```

- [ ] **Step 3:** `get_settings`/`patch_settings` 에서 `_email, cid = require_couple(request)`; 모든 `kv_set(key, v)` → `kv_set(cid, key, v)`; `ensure_special_events()` → `ensure_special_events(cid)`; `clear_auto(prefix)` → `clear_auto(cid, prefix)`; 반환 `_current(cid)`.
- [ ] **Step 4:** `/dday` 도 `_email, cid = require_couple(request)` 로 바꾸고(인자 추가) 모든 kv 에 cid 전달. (랜딩 익명 dday 는 제거 — match/login 화면엔 불필요.)
- [ ] **Step 5: Commit**

```bash
git add app/routes/settings_routes.py
git commit -m "feat(settings): 커플 스코프 kv + dday"
```

### Task 5.2: special_events 커플 스코프 (TDD)

**Files:** Modify `app/special_events.py`; Test `tests/test_birthday_dday.py`(갱신)

- [ ] **Step 1:** `_upsert`·`clear_auto`·`ensure_special_events` 에 `couple_id` 추가:

```python
def _upsert(couple_id: int, eid: str, title: str, due: date, color: str) -> int:
    with cursor() as cur:
        if cur.execute("SELECT 1 FROM events WHERE id=? AND couple_id=?", (eid, couple_id)).fetchone():
            return 0
        cur.execute(
            "INSERT INTO events (id, title, due, time, note, color, source, done, "
            "reminder_minutes, owner_email, couple_id, created_at) "
            "VALUES (?, ?, ?, NULL, NULL, ?, 'auto', 0, NULL, '', ?, ?)",
            (eid, title, due.isoformat(), color, couple_id,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
    return 1


def clear_auto(couple_id: int, prefix: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE couple_id=? AND source='auto' AND id LIKE ?",
                    (couple_id, prefix + "%"))


def ensure_special_events(couple_id: int, today: date | None = None) -> int:
    today = today or date.today()
    ...
    start = _parse(kv_get(couple_id, "anniversary_date", cfg.anniversary_date))
    ...
            created += _upsert(couple_id, f"auto-mile-{m}", f"💞 {m}일", d, "#ec4899")
    ...
        born = _parse(kv_get(couple_id, bkey, bdef))
        nick = kv_get(couple_id, nkey, ndef)
        ...
            created += _upsert(couple_id, f"auto-bday-{who}-{yr}", f"🎂 {nick} 생일", d, "#f59e0b")
```

> `auto-*` id 는 커플 간 동일하지만 `_upsert`/`clear_auto` 가 couple_id 로 가드하므로 충돌 없음. (events.id 는 전역 PK 라 같은 id 가 두 커플에 동시에 들어갈 수 없다 — 그러나 한 베타 DB 안에서는 충돌이 문제될 수 있다.)
>
> **중요 결정:** events.id 전역 유일 제약 때문에 `auto-mile-100` 을 두 커플이 가질 수 없다. → `_upsert` 의 eid 를 커플별로 네임스페이스: 호출 시 `f"c{couple_id}-auto-mile-{m}"` 형태로 바꾼다. `clear_auto` prefix 도 `f"c{couple_id}-{prefix}"`. settings_routes 의 stale prefix(`auto-mile-` 등)는 `clear_auto(cid, prefix)` 가 내부에서 `c{cid}-` 를 붙이도록 통일한다.

- [ ] **Step 1b:** 위 네임스페이스 반영 — `_upsert`/`clear_auto` 내부에서 `eid`/`prefix` 앞에 `f"c{couple_id}-"` 를 붙이고, `ensure_special_events` 의 id 들은 접두사 없이 넘긴다(헬퍼가 붙임). 예:

```python
def _upsert(couple_id, eid, title, due, color):
    eid = f"c{couple_id}-{eid}"
    ...
def clear_auto(couple_id, prefix):
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE couple_id=? AND source='auto' AND id LIKE ?",
                    (couple_id, f"c{couple_id}-{prefix}%"))
```

- [ ] **Step 2:** `tests/test_birthday_dday.py` 를 읽고, `ensure_special_events()`/`clear_auto()` 호출에 `couple_id=1` 인자를 추가하도록 갱신. (conftest 가 couple #1 시드.)

- [ ] **Step 3: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_birthday_dday.py -q` → PASS

```bash
git add app/special_events.py tests/test_birthday_dday.py
git commit -m "feat(special_events): 커플 스코프 + id 네임스페이스"
```

### Task 5.3: server.index 3분기 + reminder_loop 커플별

**Files:** Modify `server.py`

- [ ] **Step 1:** import 에 `couple_of` 추가:

```python
from app.auth import read_session, couple_of
from app.db import kv_get, couple_members
```

- [ ] **Step 2:** `index()` 교체:

```python
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    email = read_session(request)
    if not email:
        return templates.TemplateResponse(
            request, "login.html",
            {"asset_v": _asset_version()},
            headers={"Cache-Control": "no-cache, must-revalidate"})
    cid = couple_of(email)
    if not cid:
        return templates.TemplateResponse(
            request, "match.html",
            {"email": email, "asset_v": _asset_version()},
            headers={"Cache-Control": "no-cache, must-revalidate"})
    return templates.TemplateResponse(
        request, "app.html",
        {
            "kakao_js_key": settings.kakao_js_key,
            "anniversary": kv_get(cid, "anniversary_date", settings.anniversary_date),
            "nickname_a": kv_get(cid, "nickname_a", settings.nickname_a),
            "nickname_b": kv_get(cid, "nickname_b", settings.nickname_b),
            "mascot": kv_get(cid, "mascot", "bunny"),
            "theme": kv_get(cid, "theme", "rosy"),
            "email": email,
            "partner_configured": True,
            "asset_v": _asset_version(),
        },
        headers={"Cache-Control": "no-cache, must-revalidate"})
```

> `login.html`/`match.html` 은 추가 컨텍스트 변수를 거의 안 쓰므로 최소 컨텍스트만 넘긴다. (login.html 은 `_mascot_bunny.svg` include 만 필요.)

- [ ] **Step 3:** `_reminder_loop` 를 커플별로:

`for em in settings.allowed_emails:` (리마인더 전송부) → `for em in couple_members(r["couple_id"]):`
그리고 `ensure_special_events` 호출을 모든 커플에 대해 돌린다:

```python
            if last_ensure_day != now.date():
                try:
                    with db_cursor() as cur:
                        cids = [row["id"] for row in cur.execute("SELECT id FROM couples").fetchall()]
                    for cid in cids:
                        ensure_special_events(cid, now.date())
                    last_ensure_day = now.date()
                except Exception as exc:
                    print(f"[special_events] error: {exc}")
```

리마인더 쿼리는 그대로(events 전체에서 미알림). 전송부만 `couple_members(r["couple_id"])` 로.

- [ ] **Step 4:** 404 핸들러의 `index(req)` 폴백은 그대로 동작(미인증→login).

- [ ] **Step 5: 통과(전체) + Commit**

Run: `python3.11 -m pytest -q`
Expected: 기존+신규 전부 PASS (login/match 템플릿이 아직 없으면 index 테스트가 깨질 수 있음 — match.html 은 Phase 6 에서. 그 전까지 `test_access_auth::test_index_serves_app...` 는 matched 사용자라 app.html 정상).

```bash
git add server.py
git commit -m "feat(server): index 3분기(login/match/app) + 커플별 reminder"
```

### Task 5.4: auth_routes `/me` 커플 기반

**Files:** Modify `app/routes/auth_routes.py`

- [ ] **Step 1:** `me` 교체:

```python
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
    is_a = members and email == members[0]
    return {
        "authenticated": True,
        "email": email,
        "matched": True,
        "partner": partner_of(email),
        "nickname_self": kv_get(cid, "nickname_a" if is_a else "nickname_b", None),
        "nickname_partner": kv_get(cid, "nickname_b" if is_a else "nickname_a", None),
    }
```

- [ ] **Step 2:** `tests/test_access_auth.py` 는 `/me` 가 matched(couple #1) 라 `authenticated True`·`email` 만 검증 — 그대로 통과. 확인:

Run: `python3.11 -m pytest tests/test_access_auth.py -q` → PASS

- [ ] **Step 3: Commit**

```bash
git add app/routes/auth_routes.py
git commit -m "feat(auth_routes): /me 커플/매칭 기반 응답"
```

---

## Phase 6 — UI / 템플릿

### Task 6.1: match.html (미매칭 화면)

**Files:** Create `templates/match.html`

- [ ] **Step 1: 작성** — login.html 스타일(rose 그라데이션·마스코트) 차용. Alpine 컴포넌트로 초대/받은초대/보낸초대/로그아웃.

> **외부 스크립트 SRI:** 기존 `login.html` 컨벤션을 그대로 따른다 — Alpine 은 self-host(`/static/js/alpine.min.js`)로 CDN 컴프로마이즈를 피하고, Tailwind **Play CDN** 은 브라우저 동적 컴파일이라 SRI 적용 불가(login.html 주석에 기록됨). 프로덕션화 시 Tailwind 정적 빌드로 교체하면 SRI 부여 가능 — 이는 베타 범위 밖.

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<meta name="theme-color" content="#fda4af" />
<title>커플 맺기 · couple.ai-ve.uk</title>
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css" rel="stylesheet">
<script src="https://cdn.tailwindcss.com"></script>
<style> body{font-family:'Pretendard Variable','Pretendard',-apple-system,sans-serif;} </style>
</head>
<body class="min-h-screen bg-gradient-to-br from-rose-100 via-pink-50 to-amber-50">
<div class="min-h-screen flex items-center justify-center px-5 py-10">
  <main class="w-full max-w-md bg-white/80 backdrop-blur-2xl rounded-[2rem] p-7 shadow-xl ring-1 ring-white/60"
        x-data="matchApp()" x-init="load()">
    <div class="flex justify-center -mt-14 mb-2"><div>{% include "_mascot_bunny.svg" %}</div></div>
    <h1 class="text-center text-2xl text-rose-600 font-bold mb-1">아직 둘이 되기 전이에요</h1>
    <p class="text-center text-rose-700/70 text-sm mb-5">상대의 이메일로 초대하거나, 받은 초대를 수락해 주세요.</p>

    <!-- 초대 보내기 -->
    <form @submit.prevent="invite" class="space-y-3 mb-6">
      <input type="email" x-model="email" required placeholder="상대 이메일"
        class="w-full bg-white rounded-2xl px-4 py-3 ring-1 ring-rose-200 focus:ring-2 focus:ring-rose-400 outline-none" />
      <button class="w-full bg-gradient-to-br from-rose-400 to-pink-500 text-white font-semibold py-3 rounded-2xl active:scale-[.98]">💌 커플 초대하기</button>
    </form>

    <!-- 받은 초대 -->
    <template x-if="incoming.length">
      <div class="mb-5">
        <h2 class="text-sm font-semibold text-rose-900/70 mb-2">받은 초대</h2>
        <template x-for="i in incoming" :key="i.id">
          <div class="flex items-center justify-between bg-rose-50 rounded-2xl px-4 py-3 mb-2">
            <span class="text-rose-800 text-sm" x-text="i.inviter_email"></span>
            <span class="space-x-2">
              <button @click="accept(i.id)" class="bg-rose-500 text-white text-sm px-3 py-1.5 rounded-xl">수락</button>
              <button @click="decline(i.id)" class="text-rose-500 text-sm px-2 py-1.5">거절</button>
            </span>
          </div>
        </template>
      </div>
    </template>

    <!-- 보낸 초대 -->
    <template x-if="outgoing.length">
      <div class="mb-5">
        <h2 class="text-sm font-semibold text-rose-900/70 mb-2">보낸 초대</h2>
        <template x-for="o in outgoing" :key="o.id">
          <div class="flex items-center justify-between bg-amber-50 rounded-2xl px-4 py-3 mb-2">
            <span class="text-amber-800 text-sm" x-text="o.invitee_email + ' · 대기중'"></span>
            <button @click="cancel(o.id)" class="text-amber-600 text-sm px-2 py-1.5">취소</button>
          </div>
        </template>
      </div>
    </template>

    <p x-show="msg" x-text="msg" class="text-rose-700 bg-rose-100 rounded-xl px-3 py-2 text-sm text-center mb-3"></p>
    <button @click="logout" class="w-full text-rose-600 text-sm py-2 hover:underline">로그아웃</button>
  </main>
</div>
<script src="/static/js/alpine.min.js" defer></script>
<script>
function matchApp(){
  return {
    email:'', incoming:[], outgoing:[], msg:'',
    async load(){
      const j = await (await fetch('/api/couple/invites')).json();
      this.incoming = j.incoming||[]; this.outgoing = j.outgoing||[];
    },
    _err(d){ return ({cannot_invite_self:'자기 자신은 초대할 수 없어요',
      invitee_already_matched:'상대가 이미 다른 커플이에요', already_matched:'이미 커플이에요',
      duplicate_invite:'이미 초대를 보냈어요'})[d] || '오류가 났어요'; },
    async invite(){
      this.msg='';
      const r = await fetch('/api/couple/invite',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:this.email})});
      const j = await r.json();
      if(!r.ok){ this.msg=this._err(j.detail); return; }
      this.email=''; await this.load(); this.msg='초대를 보냈어요 💌';
    },
    async accept(id){
      const r = await fetch(`/api/couple/invites/${id}/accept`,{method:'POST'});
      if(r.ok){ location.href='/'; } else { this.msg='수락 실패'; }
    },
    async decline(id){ await fetch(`/api/couple/invites/${id}/decline`,{method:'POST'}); this.load(); },
    async cancel(id){ await fetch(`/api/couple/invites/${id}/cancel`,{method:'POST'}); this.load(); },
    async logout(){ await fetch('/api/auth/logout',{method:'POST'}); location.href='/'; },
  };
}
</script>
</body>
</html>
```

- [ ] **Step 2: 게이팅 테스트** `tests/test_match_gating.py`

```python
from fastapi.testclient import TestClient
from server import app
from app import db

client = TestClient(app)


def _h(e): return {"Cf-Access-Authenticated-User-Email": e}


def test_unmatched_sees_match_screen():
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES ('m1@t', NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL")
    html = client.get("/", headers=_h("m1@t")).text
    assert "커플 초대하기" in html
    assert "인증 코드 받기" not in html        # login 아님


def test_matched_sees_app():
    html = client.get("/", headers=_h(db.settings.allowed_emails[0] if hasattr(db, "settings") else "x")).text
```

> 두 번째 테스트는 conftest couple #1 멤버로 단순화. 정확히는:

```python
from app.config import settings as cfg
def test_matched_sees_app():
    html = client.get("/", headers=_h(cfg.allowed_emails[0])).text
    assert "커플 초대하기" not in html
```

- [ ] **Step 3: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_match_gating.py -q` → PASS

```bash
git add templates/match.html tests/test_match_gating.py
git commit -m "feat(ui): match.html 미매칭 화면(초대/수락만) + 게이팅 테스트"
```

### Task 6.2: login.html 오픈가입 문구

**Files:** Modify `templates/login.html`

- [ ] **Step 1:** "허락된 두 이메일만 들어올 수 있어요." → "이메일로 가입하고, 상대를 초대해 둘만의 공간을 만들어요." 로 교체. step1 안내문(`<p class="text-xs ...">`) 문구만 변경.
- [ ] **Step 2:** `requestCode` 의 에러 매핑에서 `not_allowed` 문구를 "잠시 후 다시 시도해 주세요" 로(오픈가입이라 거의 안 뜸). 기능 변화 없음.
- [ ] **Step 3: Commit**

```bash
git add templates/login.html
git commit -m "feat(ui): login 오픈가입 문구"
```

### Task 6.3: app.html — 커플 해제 + 카카오 가져오기 진입점

**Files:** Modify `templates/app.html`

> app.html(42KB)은 Alpine 기반. 먼저 `Read templates/app.html` 로 설정(설정 탭)·장소 탭 구조와 기존 Alpine 데이터 함수명을 확인한다.

- [ ] **Step 1:** 설정 영역에 "커플 해제" 버튼 추가(확인 후 `POST /api/couple/unlink` → `location.href='/'`).

```html
<button @click="if(confirm('정말 커플을 해제할까요? 공유 데이터는 더 이상 보이지 않아요.')) unlinkCouple()"
        class="w-full mt-4 text-red-500 text-sm py-2 ring-1 ring-red-200 rounded-xl">💔 커플 해제</button>
```

Alpine 메서드:

```javascript
async unlinkCouple(){
  const r = await fetch('/api/couple/unlink', {method:'POST'});
  if(r.ok) location.href='/';
}
```

- [ ] **Step 2:** 장소 탭에 "카카오맵 폴더 가져오기" 버튼 + 모달(붙여넣기 입력 → 미리보기 목록 → 확정). Phase 7 의 API 와 연결.

```html
<button @click="kakaoImport.open=true" class="...">📥 카카오맵 폴더 가져오기</button>
<!-- 모달: import 흐름은 Task 7.3 에서 JS 와 함께 완성 -->
```

- [ ] **Step 3: Commit**

```bash
git add templates/app.html
git commit -m "feat(ui): app 설정에 커플 해제 + 장소에 카카오 가져오기 진입점"
```

---

## Phase 7 — 카카오맵 폴더 일괄 가져오기

### Task 7.0: 스파이크 — 공유 페이지 구조 확인 (차단 검증)

**Files:** (탐색만)

- [ ] **Step 1:** 사장님께 카카오맵 앱에서 저장 폴더 1개를 *공유 → 링크 복사* 해 받는다(예 `https://kko.kakao.com/xxxx`). 받기 전까지는 아래 샘플 fetch 로 구조를 추정.
- [ ] **Step 2:** 링크를 `curl -sL` 로 따라가 최종 HTML 에 장소 목록 JSON(예 `window.__NEXT_DATA__`, `apolloState`, 또는 `<script type="application/json">`)이 있는지 확인.

```bash
curl -sL "<공유링크>" -A "Mozilla/5.0" -o /tmp/kakao_folder.html
grep -o 'place_name\|placeName\|"y":\|"lat"' /tmp/kakao_folder.html | sort | uniq -c | head
```

- [ ] **Step 3: 판정** — 장소명/좌표가 HTML 내 JSON 으로 존재하면 **진행**. 동적 렌더(빈 HTML)면 **즉시 사장님께 보고**하고 대안(개별 공유 share-intent / 수동 입력)을 제안. 확인된 샘플 HTML 을 `tests/fixtures/kakao_folder.html` 로 저장(테스트 픽스처).

> 이 스파이크가 실패하면 Phase 7 의 나머지 Task 는 **보류**하고 Phase 8 로 간다(코어는 이미 완성).

### Task 7.1: 파서 (TDD, 네트워크 없음)

**Files:** Create `app/kakao_import.py`; Test `tests/test_kakao_import.py`, fixture `tests/fixtures/kakao_folder.html`

- [ ] **Step 1:** 스파이크에서 저장한 실제 구조에 맞춰 테스트 작성. (아래는 `<script type="application/json">` 에 `places:[{name,x,y,road_address}]` 가 있다고 가정한 예 — 실제 키는 7.0 결과로 교정.)

```python
from app import kakao_import

SAMPLE = '''<html><body>
<script id="data" type="application/json">
{"places":[{"name":"연남동 카페","x":"126.92","y":"37.56","road_address":"서울 마포구 ..."},
           {"name":"망원 칼국수","x":"126.90","y":"37.55","road_address":""}]}
</script></body></html>'''


def test_parse_folder_extracts_places():
    items = kakao_import.parse_kakao_folder(SAMPLE)
    assert len(items) == 2
    assert items[0]["name"] == "연남동 카페"
    assert abs(items[0]["lat"] - 37.56) < 0.01
    assert abs(items[0]["lng"] - 126.92) < 0.01


def test_parse_empty_returns_empty():
    assert kakao_import.parse_kakao_folder("<html></html>") == []
```

- [ ] **Step 2:** 실패 확인 → Run: `python3.11 -m pytest tests/test_kakao_import.py -q` → FAIL
- [ ] **Step 3:** `app/kakao_import.py` 작성(파싱 = 순수 함수; 네트워크 = 별도 async):

```python
"""카카오맵 저장 폴더 공유 링크 → 장소 후보 추출.
파싱(parse_kakao_folder)은 순수 함수라 픽스처로 단위 테스트. 네트워크는 fetch_folder."""
import json
import re

import httpx

from .config import settings


def parse_kakao_folder(html: str) -> list[dict]:
    """폴더 공유 HTML 에서 장소 목록 추출. 구조가 안 맞으면 []."""
    out: list[dict] = []
    for m in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                         html, re.DOTALL):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for p in _walk_places(data):
            try:
                out.append({
                    "name": p["name"],
                    "road_address": p.get("road_address") or p.get("address") or "",
                    "lat": float(p["y"]),
                    "lng": float(p["x"]),
                    "kakao_place_id": str(p.get("id") or ""),
                })
            except (KeyError, TypeError, ValueError):
                continue
    # 이름+좌표 중복 제거
    seen, uniq = set(), []
    for it in out:
        k = (it["name"], round(it["lat"], 5), round(it["lng"], 5))
        if k not in seen:
            seen.add(k); uniq.append(it)
    return uniq


def _walk_places(data) -> list[dict]:
    """JSON 트리에서 'name'+'x'+'y' 를 가진 dict 들을 모은다(구조 변화에 견고)."""
    found = []
    def rec(node):
        if isinstance(node, dict):
            if "name" in node and "x" in node and "y" in node:
                found.append(node)
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for v in node:
                rec(v)
    rec(data)
    return found


async def fetch_folder(url: str) -> str:
    """공유 링크(짧은링크 포함) 최종 HTML."""
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


async def geocode(name: str) -> dict | None:
    """좌표 없는 항목 보강 — Kakao Local 키워드 검색 첫 결과."""
    if not settings.kakao_rest_key:
        return None
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://dapi.kakao.com/v2/local/search/keyword.json",
                        params={"query": name, "size": 1},
                        headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"})
    if r.status_code != 200:
        return None
    docs = r.json().get("documents", [])
    if not docs:
        return None
    d = docs[0]
    return {"lat": float(d["y"]), "lng": float(d["x"]),
            "road_address": d.get("road_address_name") or d.get("address_name") or ""}
```

- [ ] **Step 4: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_kakao_import.py -q` → PASS

```bash
git add app/kakao_import.py tests/test_kakao_import.py tests/fixtures/kakao_folder.html
git commit -m "feat(kakao): 폴더 공유 HTML 파서 + 지오코딩(격리·테스트)"
```

### Task 7.2: import 라우트 (미리보기 → 확정)

**Files:** Modify `app/routes/places_routes.py`

- [ ] **Step 1:** import 추가: `from .. import kakao_import`
- [ ] **Step 2:** 두 엔드포인트 추가:

```python
class KakaoUrlIn(BaseModel):
    url: str


class KakaoConfirmIn(BaseModel):
    items: list[dict]
    kind: str = "wishlist"


@router.post("/import/kakao")
async def import_kakao(body: KakaoUrlIn, request: Request):
    _email, _cid = require_couple(request)
    try:
        html = await kakao_import.fetch_folder(body.url)
    except Exception:
        raise HTTPException(status_code=400, detail="fetch_failed")
    items = kakao_import.parse_kakao_folder(html)
    if not items:
        raise HTTPException(status_code=422, detail="no_places_parsed")
    return {"count": len(items), "items": items}      # 아직 insert 안 함 (미리보기)


@router.post("/import/kakao/confirm")
async def import_kakao_confirm(body: KakaoConfirmIn, request: Request):
    user, cid = require_couple(request)
    if body.kind not in ("visited", "wishlist", "revisit"):
        raise HTTPException(status_code=400, detail="bad_kind")
    added = 0
    with cursor() as cur:
        for it in body.items:
            name = (it.get("name") or "").strip()
            lat, lng = it.get("lat"), it.get("lng")
            if not name or lat is None or lng is None:
                continue
            dup = cur.execute(
                "SELECT 1 FROM places WHERE couple_id=? AND name=? "
                "AND ABS(lat-?)<0.0005 AND ABS(lng-?)<0.0005",
                (cid, name, lat, lng)).fetchone()
            if dup:
                continue
            cur.execute(
                """INSERT INTO places (id, name, address, lat, lng, kind, category, memo,
                   created_at, owner_email, couple_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                (str(int(_time.time() * 1000)) + str(added), name,
                 it.get("road_address") or "", float(lat), float(lng), body.kind,
                 it.get("category") or "", datetime.now().isoformat(timespec="seconds"),
                 user, cid))
            added += 1
    return {"ok": True, "added": added}
```

- [ ] **Step 3:** 테스트 추가 `tests/test_kakao_import.py` (confirm 의 중복 스킵·스코프):

```python
from fastapi.testclient import TestClient
from server import app
from app import db, couples, auth

client = TestClient(app)
def _h(e): return {"Cf-Access-Authenticated-User-Email": e}


def test_confirm_inserts_and_skips_dupes():
    for em in ("k1@t", "k2@t"):
        with db.cursor() as cur:
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (em,))
    inv = couples.create_invite("k1@t", "k2@t"); couples.accept_invite("k2@t", inv["id"])
    items = [{"name": "샘플카페", "lat": 37.5, "lng": 127.0}]
    r1 = client.post("/api/places/import/kakao/confirm",
                     json={"items": items, "kind": "wishlist"}, headers=_h("k1@t"))
    assert r1.json()["added"] == 1
    r2 = client.post("/api/places/import/kakao/confirm",
                     json={"items": items, "kind": "wishlist"}, headers=_h("k1@t"))
    assert r2.json()["added"] == 0      # 중복 스킵
```

- [ ] **Step 4: 통과 + Commit**

Run: `python3.11 -m pytest tests/test_kakao_import.py -q` → PASS

```bash
git add app/routes/places_routes.py tests/test_kakao_import.py
git commit -m "feat(kakao): import 미리보기/확정 라우트 + 중복 스킵"
```

### Task 7.3: 카카오 가져오기 UI (app.html 모달)

**Files:** Modify `templates/app.html`

- [ ] **Step 1:** Task 6.3 의 진입 버튼에 모달 + Alpine 흐름 연결:

```javascript
kakaoImport: { open:false, url:'', items:[], picked:[], kind:'wishlist', loading:false, msg:'' },
async kakaoPreview(){
  this.kakaoImport.loading=true; this.kakaoImport.msg='';
  try{
    const r = await fetch('/api/places/import/kakao',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({url:this.kakaoImport.url})});
    const j = await r.json();
    if(!r.ok){ this.kakaoImport.msg = j.detail==='no_places_parsed'?'장소를 못 읽었어요(공유 링크 확인)':'가져오기 실패'; return; }
    this.kakaoImport.items = j.items;
    this.kakaoImport.picked = j.items.map((_,i)=>i);   // 기본 전체 선택
  } finally { this.kakaoImport.loading=false; }
},
async kakaoConfirm(){
  const sel = this.kakaoImport.picked.map(i=>this.kakaoImport.items[i]);
  const r = await fetch('/api/places/import/kakao/confirm',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({items:sel, kind:this.kakaoImport.kind})});
  const j = await r.json();
  this.kakaoImport.msg = `${j.added}곳 추가됐어요 📍`;
  this.kakaoImport.open=false; this.loadPlaces && this.loadPlaces();   // 목록 갱신
},
```

모달 마크업(붙여넣기 입력 → 미리보기 체크박스 목록 → kind 선택 → 확정). 기존 app.html 의 모달 패턴을 따른다.

- [ ] **Step 2:** Playwright 스모크(선택): 모달 열림·미리보기 호출만 확인. (webapp-testing 스킬 사용 가능.)
- [ ] **Step 3: Commit**

```bash
git add templates/app.html
git commit -m "feat(ui): 카카오 폴더 가져오기 모달(미리보기→확정)"
```

---

## Phase 8 — 베타 배포 산출물

### Task 8.1: seed 스크립트

**Files:** Create `scripts/seed_beta_db.py`

- [ ] **Step 1: 작성** — 운영 DB 를 WAL 체크포인트 후 파일 복제(읽기전용). couple #1 부트스트랩은 베타 첫 기동의 `_migrate` 가 수행.

```python
"""운영 couple.db 를 베타 DB 로 안전 복제(읽기전용). 실행:
    python3.11 scripts/seed_beta_db.py
운영 DB 는 절대 수정하지 않는다."""
import shutil
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "data" / "couple.db"
DST = BASE / "data" / "couple_beta.db"


def main():
    if not SRC.exists():
        raise SystemExit(f"운영 DB 없음: {SRC}")
    if DST.exists():
        raise SystemExit(f"이미 존재: {DST} (덮어쓰려면 먼저 삭제)")
    # 읽기전용으로 열어 WAL 체크포인트 → 일관 스냅샷 보장
    conn = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass  # 읽기전용이면 스킵 — backup API 로 대체
    conn.close()
    # 안전 복제: sqlite backup API (WAL 포함 일관 복사)
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    dst = sqlite3.connect(DST)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    print(f"복제 완료: {DST}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 검증(실행은 사장님 확인 후)** — 문법만:

Run: `python3.11 -c "import ast; ast.parse(open('scripts/seed_beta_db.py').read()); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_beta_db.py
git commit -m "feat(deploy): 베타 DB 안전 복제 시드 스크립트"
```

### Task 8.2: beta systemd + 안내 문서

**Files:** Create `deploy/couple-beta.service`, `deploy/BETA.md`

- [ ] **Step 1:** `deploy/couple-beta.service`:

```ini
[Unit]
Description=couple BETA (beta.couple.ai-ve.uk — 멀티커플 테스트, FastAPI)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=opc
Group=opc
WorkingDirectory=/home/opc/projects/couple
ExecStart=/usr/bin/python3.11 -m uvicorn server:app --host 127.0.0.1 --port 8801 --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=couple-beta
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/opc/.local/bin
Environment=PYTHONUNBUFFERED=1
Environment=COUPLE_DB=/home/opc/projects/couple/data/couple_beta.db
Environment=SESSION_COOKIE=couple_beta_session
Environment=OPEN_SIGNUP=1

[Install]
WantedBy=multi-user.target
```

> 같은 코드 디렉터리에서 `beta` 브랜치를 체크아웃해 띄우면 운영(main, 8800)과 코드가 섞인다. **권장:** `git worktree add ../couple-beta beta` 로 별도 워크트리를 만들고 `WorkingDirectory=/home/opc/projects/couple-beta` 로 가리킨다. BETA.md 에 명시.

- [ ] **Step 2:** `deploy/BETA.md` — 사장님 액션 체크리스트:

```markdown
# 베타 배포 절차 (beta.couple.ai-ve.uk)

> 모든 단계는 사장님이 실행. 코드/AI 는 실행하지 않는다.

## 1. 별도 워크트리 (코드 격리)
    git worktree add /home/opc/projects/couple-beta beta

## 2. 베타 DB 시드 (운영 DB 는 읽기전용 복제)
    cd /home/opc/projects/couple-beta
    python3.11 scripts/seed_beta_db.py        # data/couple_beta.db 생성

## 3. systemd 등록
    sudo cp deploy/couple-beta.service /etc/systemd/system/
    # WorkingDirectory 를 /home/opc/projects/couple-beta 로 수정
    sudo systemctl daemon-reload
    sudo systemctl enable --now couple-beta.service
    systemctl status couple-beta.service       # 127.0.0.1:8801

## 4. Cloudflare 대시보드 (Zero Trust → Networks → Tunnels → couple-tunnel)
- Public Hostname 추가: `beta.couple.ai-ve.uk` → `http://127.0.0.1:8801`
- 그 호스트에 대한 **Access 정책 해제**(오픈 가입 테스트). WS 경로 `/ws` 동일 서비스라 별도 설정 불필요.

## 5. 확인
- https://beta.couple.ai-ve.uk 접속 → login → 신규 이메일 코드 로그인(콘솔 코드는 `journalctl -u couple-beta`)
- 두 계정으로 초대→수락→데이터 격리 확인.

## 롤백
    sudo systemctl disable --now couple-beta.service
    git worktree remove /home/opc/projects/couple-beta
```

- [ ] **Step 3: Commit**

```bash
git add deploy/couple-beta.service deploy/BETA.md
git commit -m "feat(deploy): beta systemd + 배포 절차 문서"
```

### Task 8.3: 전체 회귀 + 정리

- [ ] **Step 1:** 전체 테스트

Run: `python3.11 -m pytest -q`
Expected: 전부 PASS (신규 6종 + 기존 갱신).

- [ ] **Step 2:** 미사용 import 정리(`require_user` 가 안 쓰이게 된 파일 등), `partner_of` import 유지 확인.

- [ ] **Step 3:** 사장님께 배포 절차(`deploy/BETA.md`) 안내 후 **확인 받고** 서비스 기동.

```bash
git add -A
git commit -m "chore: 멀티커플 베타 정리 및 전체 회귀 통과"
```

---

## Self-Review (작성자 점검 결과)

**Spec coverage:**
- 로그인(이메일+코드) → 기존 유지, open_signup 으로 개방 ✅ (Task 0.1, 2.1)
- 커플별 세션 분리(멀티테넌시) ✅ (Phase 1·4·5)
- 아이디(이메일)로 초대 ✅ (Task 3.1, 3.2)
- 수락 ✅ / 거절·취소 ✅ (3.1, 3.2)
- 미매칭 시 초대/수락만(a) ✅ (Task 5.3 index 분기 + 6.1 match.html)
- 미가입자 선초대(b) ✅ (create_invite 가 invitee 미존재 허용; test_preinvite)
- 커플 해제(c) ✅ (3.1 unlink, 3.2 라우트, 6.3 UI)
- 카카오 폴더 일괄 가져오기 ✅ (Phase 7, 스파이크 게이트 포함)
- 베타 서브도메인/별도 DB/OPEN_SIGNUP ✅ (Phase 0, 8)

**Placeholder scan:** Task 4.5(photos)·6.3·7.3 은 "먼저 Read 후 동일 패턴 적용" 지시 — 이는 대상 파일(미독본/대형)에 한정한 정당한 절차이며, 적용할 변환 규칙과 대표 코드를 구체적으로 명시함. 그 외 placeholder 없음.

**Type consistency:**
- `kv_get(couple_id, key, default)` / `kv_set(couple_id, key, value)` — 전 호출부 통일(1.3 정의, 4.7·4.8·5.1·5.2·5.3·5.4 에서 일관 사용).
- `require_couple(request) -> (email, cid)` — 전 라우트 동일 언패킹.
- `execute_tool(name, args, user_email, couple_id)` — chat_routes(4.7)·agent_tools(4.8) 시그니처 일치.
- `ensure_special_events(couple_id, today=None)` / `clear_auto(couple_id, prefix)` — settings(5.1)·special_events(5.2)·server(5.3) 일치.

**알려진 위험(스펙 §11 반영):** 카카오 파싱 취약성 → 7.0 스파이크 게이트로 조기 차단; settings_kv 복합키 재작성 멱등 가드; events.id 전역PK 충돌 → `c{cid}-` 네임스페이스(5.2).

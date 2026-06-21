# Cloudflare Access → 자체 회원가입·로그인(아이디+비밀번호) 전환

- 작성일: 2026-06-21
- 브랜치: `feat-password-auth`
- 상태: 설계 승인됨 → 구현 계획

## 배경

현재 인증은 **두 겹**이고 운영에선 사실상 1겹만 동작한다.

1. **Cloudflare Access (실제 게이트)** — 사이트는 cloudflared 터널(`couple.ai-ve.uk → 127.0.0.1:8800`) 뒤에 있다. origin 은 `127.0.0.1` 바인드라 터널 밖에서 직접 접근 불가. CF Access 가 신원을 검증하면 origin 요청에 `Cf-Access-Authenticated-User-Email` 헤더를 강제 주입한다. 운영은 `OPEN_SIGNUP=1` 이라 앱 화이트리스트가 꺼져 CF Access 가 유일한 문지기다.
2. **앱 자체 OTP (만들어졌지만 우회됨)** — `app/auth.py` 에 6자리 이메일 코드 로그인이 있으나, `read_session()` 이 `_access_email()` 헤더 폴백으로 자동 로그인시켜서 운영에선 OTP 폼을 거의 안 본다.

**전환 목표**: Cloudflare Access(및 그 이메일 인증)를 **완전히 버리고**, 앱 자체 **아이디(username)+비밀번호** 회원가입·로그인으로 대체한다. 가입은 **완전 개방**(이메일 소유 인증 없음).

### 결정된 요구사항 (사장 확인 완료)

- 자격증명 스킴: **아이디(username) + 비밀번호**.
- 이메일 인증: **안 함(완전 개방)**.
- 기존 운영 데이터(커플 #1, 사진·일정): **보존**. 옛 계정을 새 로그인 수단으로 이어받게 한다.

## 핵심 설계: identity / login 분리 (별칭 모델)

모든 데이터 테이블은 식별자(email 값)를 계정 키로 참조한다(`couples.member_a/b`, `*_email`, `couple_id` 대상 등 12+ 컬럼). 이 **식별자 컬럼은 그대로 두고**, 로그인 수단만 새 `username` 컬럼으로 더한다.

- `email` (PK) = **불변 identity**. 모든 데이터가 이걸 참조. 변경 없음.
- `username` (UNIQUE, 신규) = **로그인 아이디**. 로그인 시 username → 행의 `email` identity 를 세션에 적재.
- `password_hash` (신규) = argon2id 해시.

규칙:
- **신규 가입**: `email = username = <선택값>`, `password_hash` 설정 → identity == username (균일 처리).
- **로그인**: `SELECT email, password_hash FROM users WHERE username=?` → 검증 → 세션엔 그 행의 `email` identity.
- **옛 계정 이어받기(claim)**: 기존 `email` 행에 `username`/`password_hash` 만 세팅 → 옛 사진·일정·커플 **그대로 보존**, 12개 데이터 컬럼 재작성 0건.

이로써 username 은 "현관 열쇠", email 은 "집 주소(데이터 키)"로 분리된다.

## 1. DB 스키마 (`app/db.py`)

`users` 테이블에 두 컬럼 멱등 추가:

```sql
-- SCHEMA 의 users CREATE TABLE 에 컬럼 추가:
--   username TEXT,            -- 로그인 아이디 (UNIQUE 인덱스로 강제)
--   password_hash TEXT
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username)
    WHERE username IS NOT NULL;   -- 부분 유니크: 미claim 레거시 행(username NULL) 다수 허용
```

`_migrate()` 에 멱등 ALTER 추가(기존 패턴과 동일):
```python
for col in ("username", "password_hash"):
    if col not in user_cols:
        cur.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
# 부분 유니크 인덱스 생성(IF NOT EXISTS)
```

- **부분 유니크 인덱스**가 핵심: 레거시 미claim 행은 `username=NULL` 이 여러 개일 수 있어 일반 UNIQUE 면 두 번째 NULL 삽입이 막힐 수 있다(SQLite 는 NULL 을 distinct 취급하지만 안전하게 partial index 로 명시). claim 후엔 username 이 중복 불가.
- `login_codes` 테이블: 미사용으로 남긴다(무해, 드롭 안 함 — 멱등성·롤백 안전).

## 2. 인증 모듈 (`app/auth.py`)

### 제거
- `_access_email()`, `ACCESS_EMAIL_HEADER` — **fail-open 급소 제거(이번 작업의 최우선 보안 항목)**.
- OTP 일체: `issue_code`, `consume_code`, `deliver_code`, `smtplib`/`MIMEText` import.
- `is_allowed()` — 화이트리스트 개념 폐기(아래 `read_session` 에서 게이트 제거).

### 추가
```python
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
_ph = PasswordHasher()   # argon2id 기본

def hash_password(pw: str) -> str: ...
def verify_password(hash_: str, pw: str) -> bool:   # VerifyMismatchError → False

def validate_username(u: str) -> str:   # 정규화·검증, 실패 시 ValueError("bad_username")
def validate_password(pw: str) -> None: # 길이 등, 실패 시 ValueError("weak_password")

def create_user(username: str, password: str) -> str:
    """신규 가입: email=username 행 생성. 중복이면 ValueError("username_taken"). 반환 identity(email)."""

def authenticate(username: str, password: str) -> str | None:
    """username 으로 행 조회 → 비번 검증 → identity(email) 반환, 실패 None."""

def claim_legacy(email: str, username: str, password: str) -> str:
    """옛 email 행이 존재 & username IS NULL 이면 username/password_hash 세팅. 반환 identity(email).
    아니면 ValueError("not_claimable")."""
```

### 수정: `read_session`
```python
def read_session(request) -> str | None:
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    try:
        data = _serializer.loads(tok)
    except BadSignature:
        return None
    return data.get("email") if data else None
```
- **헤더 폴백·`is_allowed` 게이트 제거.** 우리 `SECRET_KEY` 로 서명된 쿠키는 우리가 비번 검증 후에만 발급하므로, 서명만 유효하면 신뢰한다.
- `make_session_cookie`, `require_user`, `couple_of`, `partner_of`, `require_couple` 는 **유지**(identity 가 email 이라는 가정 그대로).

### 로그인 throttle (무차별 대입 방어)
CF Access 가 사라지면 로그인 폼이 공개되므로 필수. 가벼운 인메모리 방식:
- key = `username`(또는 username+client_ip), 실패 횟수·최초실패시각 추적.
- N회(예: 5) 연속 실패 시 잠금창(예: 60초) 동안 423/429. 성공 시 리셋.
- 단일 프로세스 uvicorn 기준 인메모리로 충분(재시작 시 리셋되는 점 주석 명시). 정책 세부(N·잠금시간·키)는 구현 시 결정.

## 3. 라우트 (`app/routes/auth_routes.py`)

`prefix="/api/auth"`. EmailStr 제거, username/password 는 제약 `str`.

- `POST /signup` `{username, password}` → `create_user` → 세션쿠키 set → `{ok, email}`. `settings.allow_signup=False` 면 403.
- `POST /login` `{username, password}` → throttle 확인 → `authenticate` → 세션쿠키 set. 실패 401(`invalid_credentials`), 잠금 429.
- `POST /claim` `{email, username, password}` → `settings.allow_legacy_claim` True 이고 claim 가능 시 `claim_legacy` → 세션쿠키 set. 아니면 403/400.
- `POST /logout` — 유지(쿠키 삭제).
- `GET /me` — 유지(로직 동일; identity 가 username/email).

세션쿠키 옵션은 기존과 동일: `max_age=90일, httponly=True, samesite="lax", secure=False`(cloudflared 가 TLS 종단).

## 4. 설정 (`app/config.py`)

- 추가: `allow_signup: bool = env(ALLOW_SIGNUP, 기본 True)` — 가입 킬스위치.
- 추가: `allow_legacy_claim: bool = env(ALLOW_LEGACY_CLAIM, 기본 True)` — 두 분 이어받기 끝나면 끔.
- `allowed_emails`, `open_signup`, `smtp_*` — 미사용화(코드에서 참조 제거). 필드 자체는 즉시 삭제하지 않아도 무방하나, 혼선 방지를 위해 미사용 주석 또는 제거.

## 5. 로그인 화면 (`templates/login.html`)

OTP 2단계 폼 → **3-모드 토글 폼**(Alpine.js): `로그인` / `회원가입` / `기존 계정 이어받기`. 현재 토끼·하트·glass 디자인 유지.
- 로그인: username + password → `POST /login`.
- 회원가입: username + password(+확인) → `POST /signup`.
- 이어받기: 옛 이메일 + 새 username + password → `POST /claim`. (작은 안내문: "예전에 쓰던 이메일로 사진·일정 이어받기")
- 성공 시 `location.href='/'`.
- 에러 메시지 한글 매핑(`invalid_credentials`, `username_taken`, `weak_password`, `not_claimable`, 429 잠금 등).

## 6. 프론트 (`static/js/app.js`)

- `logout()` (현재 `location.href='/cdn-cgi/access/logout'`) → `await fetch('/api/auth/logout', {method:'POST'})` 후 `location.href='/'`.
- `/api/auth/me` 호출부는 변경 없음.

## 7. 서버 (`server.py`)

- WebSocket `/ws` 의 `read_session(fake_request)` 는 쿠키 기반이라 동작 유지. Cf-Access 폴백 관련 주석만 정리.
- `index()` 변경 없음(read_session 의존).

## 8. 모바일 (`mobile/.../MainActivity.kt`)

- `isInAppHost()` 에서 `cloudflareaccess.com`, `accounts.google.com`/`.google.com`/`.gstatic.com`/`.googleusercontent.com`, `challenges.cloudflare.com` 제거(이제 불필요).
- **기존 APK 는 재빌드 없이도 동작**(로그인이 동일 출처 `couple.ai-ve.uk` 에서 일어남 → 이미 in-app 호스트). 호스트 정리·재빌드는 선택(권장).

## 9. 배포 / 문서

- `deploy/couple.service`: "CF Access 절대 끄지 말 것(fail-open)" 주석 → "앱이 자체 인증하므로 CF Access **꺼도 안전**. 터널은 전송용으로 유지" 로 수정. `OPEN_SIGNUP` env 는 미사용(제거 또는 무해 유지).
- `deploy/BETA.md` 등 CF Access 게이트 언급 갱신.
- **배포 전 점검**: 운영 `.env` 의 `SECRET_KEY` 가 강한 무작위값인지 확인(기본 `dev-insecure-change-me` 면 세션 위조 위험). 약하면 회전(회전 시 기존 세션 전부 무효 → 재로그인).

## 10. 테스트

기존 8개 파일이 `Cf-Access-Authenticated-User-Email` 헤더로 인증(각 파일에 `_h()` 헬퍼). 헤더 신뢰 제거로 전부 깨지므로:

- **conftest 에 쿠키 기반 헬퍼 추가**: `auth_cookie(email)` → `{"Cookie": f"{SESSION_COOKIE}={make_session_cookie(email)}"}`. 각 파일 `_h()` 를 이 쿠키 방식으로 교체(파일당 1줄). identity 문자열은 그대로(`n1@t` 등)라 다운스트림 무변경.
- `test_access_auth.py` → `test_password_auth.py` 재작성:
  - 헤더는 **더 이상 인증 안 됨**(`Cf-Access-...` 헤더만 줘도 `me.authenticated==False`).
  - signup → 쿠키 발급 → `/me` authenticated.
  - login: 옳은/틀린 비번, 없는 username, throttle 잠금.
  - duplicate signup → 409/400.
  - claim: 레거시 행 이어받기 성공 → 옛 couple_id 보존 확인 / 이미 claim 된 행 거부 / 존재하지 않는 email 거부.
  - logout → 쿠키 무효.
- `test_auth_couple.py`: `is_allowed`/`open_signup` 테스트 제거(폐기된 개념). `couple_of`/`partner_of` 테스트는 유지.
- conftest `_seed_couple_one`: 유지(커플 #1 시드). claim 테스트는 시드된 레거시 행을 이어받는 시나리오로 활용 가능.
- `python3.11 -m pytest` 전체 green 목표.

## 비목표 (YAGNI)

- 비밀번호 재설정/찾기(이메일 인증 없으니 자동 재설정 불가 — 추후 별도).
- 비밀번호 변경 UI(추후).
- 이메일 소유 인증/2FA.
- identity 컬럼 전체 rename(B안) — 데이터 보존 + 별칭 모델로 불필요.

## 롤아웃 순서(요약)

1. DB 마이그레이션(컬럼·인덱스) → 2. auth 모듈 교체 → 3. 라우트 → 4. 템플릿/프론트 → 5. 테스트 green → 6. (선택) 모바일 재빌드 → 7. 배포(`SECRET_KEY` 확인, 서비스 재시작) → 8. 두 분 `claim` 으로 이어받기 → 9. `ALLOW_LEGACY_CLAIM=0` 으로 닫기 → 10. Cloudflare Access 정책 해제.

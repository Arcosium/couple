# 멀티커플 로그인 · 세션 분리 · 초대/수락/해제 · 카카오 일괄 가져오기 (beta)

- 작성일: 2026-06-12
- 대상 브랜치: `beta` (배포: `beta.couple.ai-ve.uk`, 별도 서비스/DB)
- 상태: 설계 확정 (구현 계획 대기)

## 1. 배경 / 문제

현재 `couple` 앱은 **완전한 단일 커플(single-tenant)** 구조다.

- 파트너는 `ALLOWED_EMAILS` 환경변수(고정 2명)에서 `partner_of()` 가 끌어온다.
- 로그인은 그 2개 이메일만 허용(`is_allowed` 화이트리스트) + Cloudflare Access SSO.
- 모든 데이터 쿼리에 **테넌트 필터가 없다** — `SELECT * FROM bucket / places / events` 가 전 행을 반환하고, 한 커플이 전부 공유한다.
- `settings_kv`(기념일·닉네임·마스코트·테마)도 전역 단일값이다.

목표: 여러 커플이 같은 인스턴스를 쓰되 **커플 단위로 데이터·세션이 격리**되고, 이메일 아이디로 **초대→수락**해 커플이 맺어지며, 매칭 전에는 **초대/수락 UI만** 보이고, 커플 **해제**도 가능하게 한다. 추가로 카카오맵 저장 폴더를 **공유 링크로 일괄 가져오기**.

운영 데이터 보호를 위해 이 작업은 **별도 브랜치 + 별도 서비스 + 별도 DB(베타 서브도메인)** 에서 검증한다.

## 2. 핵심 결정 (확정)

| 항목 | 결정 |
|---|---|
| 아이디/로그인 | **이메일 + 6자리 코드**(기존 방식 확장). 초대는 **이메일**로. |
| 베타 배포 | **별도 서비스(port 8801) + 새 DB(`couple_beta.db`) + 서브도메인 `beta.couple.ai-ve.uk`**. CF Access 해제, 오픈 가입. |
| 기존 데이터 | 운영 DB를 복제해 베타 DB로 만들고 기존 데이터를 **couple #1** 로 이관. **운영 DB는 불변.** |
| 미가입자 선초대 (b) | 허용. invite는 `invitee_email` 기준 저장 → 상대가 가입·로그인하면 받은 초대 노출. |
| 커플 해제 (c) | 지원. 커플 행 삭제 + 양쪽 `couple_id=NULL`. 공유 데이터는 옛 couple_id로 **보존되되 접근 불가**. |
| 미매칭 UI (a) | 별도 `match.html` 로 분기(초대/수락 전용). app.html과 분리해 권한 누수 차단. |
| 카카오 가져오기 | 저장 **폴더 공유 링크 붙여넣기** → 파싱 → 지오코딩 → **미리보기 후** 일괄 insert. 본 스펙에 포함. |

`prod(main)` 과 `beta` 는 **같은 코드**를 공유하고 환경변수(`COUPLE_DB`·`SESSION_COOKIE`·`OPEN_SIGNUP`)로만 갈린다. 베타 검증 후 main 머지가 목표.

## 3. 데이터 모델

### 3.1 신규 테이블

```sql
CREATE TABLE IF NOT EXISTS couples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_a TEXT NOT NULL,         -- 초대한 쪽 이메일
    member_b TEXT NOT NULL,         -- 수락한 쪽 이메일
    created_at TEXT NOT NULL
);
-- 커플은 '수락'된 순간에만 생성된다(두 멤버가 동시에 채워짐).

CREATE TABLE IF NOT EXISTS couple_invites (
    id TEXT PRIMARY KEY,
    inviter_email TEXT NOT NULL,
    invitee_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined | canceled
    created_at TEXT NOT NULL,
    responded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_invites_invitee ON couple_invites (invitee_email, status);
CREATE INDEX IF NOT EXISTS idx_invites_inviter ON couple_invites (inviter_email, status);
```

### 3.2 기존 테이블 변경 (멱등 `_migrate()` 로 추가)

- `users` 에 `couple_id INTEGER`(NULL=미매칭) 추가.
- `couple_id INTEGER` 추가: `events`, `places`, `bucket`, `photos`, `pokes`, `chat_messages`.
- `settings_kv` PK 를 `(couple_id, key)` 복합키로. (기존 단일 PK 테이블은 마이그레이션에서 재생성 후 기존 행을 couple_id=1로 이관.)

**부트스트랩/백필(모든 환경에서 멱등, `_migrate()` 가 담당)**: 기동 시
(1) 위 컬럼/복합키를 멱등 추가,
(2) **레거시 데이터가 있고 couples 가 비어 있으면** `ALLOWED_EMAILS` 2명으로 **couple #1** 행을 만들고 두 user 의 `couple_id=1` 설정,
(3) 기존 모든 데이터 행·settings_kv 값을 `couple_id=1` 로 백필.
이렇게 하면 베타뿐 아니라 **main 머지 후 운영 DB도** 같은 코드로 자동 정합된다(운영=오픈가입 off 인 단일 couple #1). `seed_beta_db.py` 는 couple #1 을 만들지 않고 **운영 DB 파일을 복제만** 한다(첫 기동 시 `_migrate` 가 정합).

### 3.3 무결성 규칙

- 한 사용자는 **최대 1개 커플**(1:1). `users.couple_id` 가 단일 출처(SoT), `couples` 가 사실.
- invite 가드: 자기 자신 초대 불가 / 내가 이미 매칭이면 불가 / 상대가 이미 매칭이면 불가 / 같은 (inviter,invitee) pending 중복 불가.
- accept 시: `couples` 행 생성 → 양쪽 `users.couple_id` 설정 → 해당 invite `accepted` → **두 사람과 얽힌 다른 pending invite 전부 `canceled`**.

## 4. 인증 계층 (`app/auth.py`, `app/config.py`)

- `settings.open_signup`(env `OPEN_SIGNUP`, 기본 `False`; 베타=`True`).
  - `True`: `is_allowed` 가 형식 유효한 모든 이메일 허용(오픈 가입).
  - `False`: 기존 화이트리스트 유지 → **main/운영 동작 불변**.
- `settings.db_path`, `SESSION_COOKIE` 를 env(`COUPLE_DB`, `SESSION_COOKIE`)로 읽게 → 베타/운영 분리.
- `couple_of(email) -> int | None`: `users.couple_id` 조회.
- `partner_of(email) -> str | None`: env 대신 **커플 멤버십**에서 상대 이메일.
- `require_couple(request) -> tuple[str, int]`: 미매칭이면 `HTTPException(409, "no_couple")`. 모든 데이터 라우트가 사용.
- CF Access 폴백(`_access_email`)은 유지하나 베타는 엣지에서 Access 해제 → 앱 자체 로그인이 게이트.

## 5. 라우트

### 5.1 커플/초대 (`app/routes/couple_routes.py`, 신규, prefix `/api/couple`)

- `GET  /status` → `{matched, couple_id, partner_email, partner_nickname, my_nickname}`.
- `POST /invite {email}` → 가드 후 invite 생성, 상대 온라인이면 `hub.send` 알림. (미가입 이메일도 허용 — b)
- `GET  /invites` → `{incoming:[pending to me], outgoing:[pending from me]}`.
- `POST /invites/{id}/accept` → (받은 쪽만, 양쪽 미매칭) 커플 생성·스코프 정리, 초대자 알림.
- `POST /invites/{id}/decline` → 받은 쪽이 거절.
- `POST /invites/{id}/cancel` → 보낸 쪽이 취소.
- `POST /unlink` → (매칭 상태에서) 커플 해제. 커플 행 삭제 + 양쪽 `couple_id=NULL`. 공유 데이터는 옛 couple_id로 보존(접근 불가). 응답으로 상대에게 알림.

### 5.2 카카오 일괄 가져오기 (`app/routes/places_routes.py` 확장 + `app/kakao_import.py` 신규)

- `POST /api/places/import/kakao {url}` → 짧은링크 리졸브 → 폴더 페이지 fetch → `parse_kakao_folder(html) -> [{name, road_address?, kakao_place_id?, lat?, lng?, category?}]`. 좌표 없으면 Kakao Local API 키워드 검색으로 지오코딩 → **후보 목록 반환(insert 안 함)**.
- `POST /api/places/import/kakao/confirm {items:[...], kind}` → 선택분만 couple_id 스코프로 일괄 insert, **이름+좌표 근접 중복 스킵**.
- `app/kakao_import.py`: 파싱 로직 격리. 네트워크 호출(`resolve+fetch`)과 순수 파싱(`parse_kakao_folder`)을 분리해 파서를 픽스처로 테스트 가능하게.

### 5.3 기존 데이터 라우트 테넌트 스코핑

`calendar` / `places` / `bucket` / `photos` / `poke` / `chat` + `special_events.py` + `agent_tools.py` + `server.py::_reminder_loop`:

- `require_couple` 로 couple_id 획득 → 모든 SELECT/UPDATE/DELETE 에 `WHERE couple_id=?`, INSERT 에 couple_id 채움.
- `kv_get/kv_set` 에 `couple_id` 인자 추가(커플별 설정).
- 실시간 브로드캐스트·리마인더 수신자를 `allowed_emails`(env) 대신 **그 커플 멤버**로.
- `auth_routes.py::me` 의 닉네임 매핑을 커플별 kv 기반으로 교체.

## 6. UI / 템플릿

`server.py::index()` 3분기:

1. 세션 없음 → `login.html`.
2. 세션 + **미매칭** → **`match.html`(신규)**: 이메일 초대 폼 + 받은 초대(수락/거절) + 보낸 초대(취소) + 로그아웃 **만**. 수락 성공 시 reload → app.html.
3. 세션 + 매칭 → `app.html`(커플별 kv 설정 주입). 설정 화면에 **커플 해제** 버튼(확인 모달) + 장소 화면에 **카카오 폴더 가져오기**(붙여넣기 → 미리보기 → 확정).

`login.html`: "허락된 두 이메일만" 문구 → 가입 안내로, `not_allowed` 처리 완화(오픈 가입 시).

## 7. 베타 배포

- `deploy/couple-beta.service`: `port 8801`, env `OPEN_SIGNUP=1`, `COUPLE_DB=.../data/couple_beta.db`, `SESSION_COOKIE=couple_beta_session`.
- `scripts/seed_beta_db.py`: 운영 `couple.db` → `couple_beta.db` **파일 복제만**(WAL 체크포인트 포함). couple #1 부트스트랩·백필은 베타 첫 기동 시 `_migrate()` 가 수행. **운영 DB 읽기 전용.**
- 사장님 액션(문서화만, 코드로 불가): CF 대시보드에서 `beta.couple.ai-ve.uk` → `127.0.0.1:8801` ingress 추가 + 해당 호스트 Access 해제.
- **서비스 재시작/배포·시드 실행은 사장님 확인 후에만**(CLAUDE.md 규칙).

## 8. 테스트 (TDD, `python3.11 -m pytest`)

- `test_couple_invite.py`: 초대 생성/수락/거절/취소, 가드(자기·중복·이미매칭), 선초대(b), 수락 시 타 pending 정리.
- `test_tenant_isolation.py`: 커플A 데이터(events/places/bucket/pokes/chat)가 커플B에 안 보임. kv 커플별 분리.
- `test_match_gating.py`: 미매칭 세션 → index 가 match 화면 렌더, 매칭 → app.
- `test_unlink.py`: 해제 후 양쪽 미매칭·데이터 접근 불가·재매칭 시 새 couple_id.
- `test_kakao_import.py`: 저장한 샘플 HTML 픽스처로 `parse_kakao_folder` 단위 테스트(네트워크 X), 지오코딩 모킹, 중복 스킵.
- `test_migration_seed.py`: 백필이 couple #1 부여, settings_kv 복합키 이관.
- 기존 테스트(`test_access_auth` 등 화이트리스트 가정)는 OPEN_SIGNUP/시드에 맞게 갱신.

## 9. 구현 순서(권장)

1. **카카오 폴더 공유 페이지 구조 확인 스파이크** — 기계 판독 가능 여부 조기 확인(비실현 시 즉시 보고).
2. DB 스키마/마이그레이션 + 시드 스크립트 (TDD).
3. 인증 계층(open_signup, couple_of, partner_of, require_couple, env 분리).
4. 커플/초대/해제 라우트.
5. 기존 라우트 테넌트 스코핑 + 실시간/리마인더.
6. 템플릿(match.html, login 문구, app 설정/해제 버튼).
7. 카카오 가져오기 라우트 + 파서 + 미리보기 UI.
8. 베타 서비스 파일/문서. (배포는 확인 후.)

## 10. 범위 밖 (YAGNI)

- APK(안드로이드) 수정 — 베타는 브라우저(서브도메인)로 검증.
- 비밀번호 인증, 소셜 로그인.
- 3인 이상/다중 커플 멤버십.
- 카카오 '개별 장소 공유(share-intent)' 경로 — 폴더 공유 링크로 충분.

## 11. 리스크

- **카카오 폴더 페이지 파싱 취약성**: 구조 변경/ToS. → 파서 격리 + 미리보기·확정 2단계 + 실패 시 폴백. 스파이크로 조기 검증.
- **마이그레이션 복합키 재생성**: settings_kv PK 변경은 테이블 재작성 — 멱등·백업(베타 DB 한정) 주의.
- **쿠키 도메인**: 베타 서브도메인은 별도 호스트라 쿠키 격리됨(쿠키명도 분리). 운영 세션과 충돌 없음.
- **CF Access 해제**: 베타는 엣지 보호가 빠지므로 앱 로그인이 유일 게이트 — fail-closed 유지(코드 화이트리스트 off지만 코드 검증은 유지).

# 온보딩(만난날짜·생일) + 오늘 한마디 + 홈 버튼 정리

- 작성일: 2026-06-12
- 브랜치: `feat-notes-onboarding` (worktree `/home/opc/projects/couple-dev`)
- 상태: 설계 승인됨 → 구현

## 배경

멀티커플 전환 후, 신규 커플은 만난날짜·생일이 비어 있다(기본값/env 의존). 커플별로 직접 입력받아야 한다.
또 일상 공유용 "오늘 한마디"(유저별 하루 1줄)를 캘린더에 추가한다. 홈의 빠른액션 버튼은 하단 탭과 중복이라 정리한다.

## 1. 온보딩 (만난날짜·생일·닉네임) — 프론트 전용, 백엔드 무변경

- **트리거**: 매칭된 사용자가 `app.html` 진입 시 본인 정보가 비어 있으면 온보딩 모달.
  - 판정: 커플 `anniversary_date` 가 비었거나, **본인 생일**이 비었으면 표시.
  - 본인이 member_a 인지 b 인지로 `birthday_a`/`birthday_b`, `nickname_a`/`nickname_b` 매핑.
    (member 판정: `/api/auth/me` 의 응답 또는 부트스트랩 시 주입된 값. 프론트는 `me` 이메일과 커플 멤버 순서로 a/b 결정 — `/api/auth/me` 에 `is_member_a` 를 추가 노출.)
  - 기존 커플 #1 은 이미 채워져 있어 안 뜸. 미입력이면 매 진입마다 안내.
- **입력 필드**: 만난날짜(둘 중 누구나 수정, 이미 값 있으면 프리필) + 본인 생일 + 본인 닉네임.
- **저장**: 기존 `PATCH /api/settings`(커플 스코프, birthday_a/b·nickname_a/b·anniversary_date 처리) 재사용. 새 API 없음.
- **백엔드 변경(소):** `app/routes/auth_routes.py::me` 응답에 `is_member_a: bool` 추가(프론트 a/b 매핑용).

## 2. 오늘 한마디 — 신규 테이블 + 라우터 + UI

### 데이터 (`app/db.py` SCHEMA 에 추가)
```sql
CREATE TABLE IF NOT EXISTS daily_notes (
    id TEXT PRIMARY KEY,
    couple_id INTEGER NOT NULL,
    author_email TEXT NOT NULL,
    date TEXT NOT NULL,                 -- YYYY-MM-DD
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (couple_id, author_email, date)
);
CREATE INDEX IF NOT EXISTS idx_notes_couple_date ON daily_notes (couple_id, date);
```
신규 테이블이라 `_migrate` 백필 불필요(SCHEMA 의 `CREATE TABLE IF NOT EXISTS` 로 충분).

### API (`app/routes/notes_routes.py`, prefix `/api/notes`, `require_couple` 스코프)
- `GET /api/notes?date=YYYY-MM-DD` → `{ "date", "mine": {content,...}|null, "partner": {content,...}|null }`
  - mine = author_email==나, partner = author_email==상대. 커플 스코프(`WHERE couple_id=?`).
- `PUT /api/notes {date, content}` → 내 한마디 upsert.
  - content 가 빈 문자열/공백이면 해당 행 **삭제**(지우기).
  - 아니면 `INSERT ... ON CONFLICT(couple_id, author_email, date) DO UPDATE SET content, updated_at`.
  - 작성/수정 시 파트너에게 `hub.send(partner, {"kind":"note", "date":..., "from":나})`.
- 날짜 형식 검증(`%Y-%m-%d`), 실패 시 400.

### UI (`templates/app.html` 캘린더 탭 + `static/js/app.js`)
- 캘린더 탭 **선택일 일정 카드 아래**에 "오늘 한마디" 카드:
  - 헤더: 선택일이 기념일(만난날짜의 월/일과 일치)이면 "오늘 같이 뭐 했어? 💞", 아니면 "오늘 뭐 했어? ✏️".
  - **내 한마디**: textarea + 저장 버튼(있으면 프리필=수정, 비우고 저장=삭제).
  - **파트너 한마디**: 읽기 전용 표시. 라벨은 `현호님`/`나`(기존 `nicks`/콕 라벨 패턴 재사용).
  - 날짜 선택(`selectDay`) 시 `GET /api/notes?date=` 로 로드. 저장 시 `PUT` 후 재로드.
  - ws `kind:"note"` 수신 시, 보고 있는 날짜와 같으면 재로드(기존 ws 핸들러에 분기 추가).
- app.js 상태: `dayNotes: {mine:'', partner:null, loading:false}`, 메서드 `loadNotes(date)`, `saveNote()`.

## 3. 홈 빠른액션 버튼 → 미니 위젯 교체

- `templates/app.html` 122–136 의 "빠른 액션 그리드"(추억/일정/버킷/지도) **삭제**.
- 그 자리에 **"오늘 한마디" 미니 위젯**: 오늘 날짜 기준, 내 한마디 한 줄 입력(인라인) + 파트너 한 줄 미리보기. 탭하면 캘린더 탭(오늘 선택)으로 이동해 전체 편집. (홈/캘린더가 같은 `dayNotes` 로직 공유; 홈은 항상 오늘.)
- 그 아래 통계 타일(사진/다녀온곳/버킷✓/장소저장)은 유지.

## 4. 테스트 (TDD, `python3.11 -m pytest`)
- `tests/test_notes.py`:
  - upsert: 같은 (couple,user,date) 두 번 PUT → 1행, content 갱신.
  - 빈 content PUT → 삭제.
  - 커플 격리: 커플A 한마디가 커플B `GET` 에 안 보임.
  - mine/partner 구분: 두 멤버가 각각 쓰면 GET 에서 올바르게 분리.
  - 미매칭 409, 날짜형식 400.
- `me` 의 `is_member_a` 노출 단위 확인(기존 `test_access_auth`/`test_auth_couple` 에 한 줄 추가 가능).
- 온보딩·홈위젯·버튼제거는 렌더 스모크(`test_match_gating` 패턴: matched 사용자 `/` 응답에 마커 존재/부재).

## 5. 배포
- 구현·테스트 완료 후 `feat-notes-onboarding` → `main` 머지, **운영(couple.service) 재시작**(사장 확인 후). `daily_notes` 는 첫 기동 시 SCHEMA 로 자동 생성.

## 범위 밖 (YAGNI)
- 한마디 사진첨부·이모지 반응·알림센터·타임라인 피드(하루 1개 모델).
- 온보딩 강제 차단(미입력이어도 앱은 사용 가능, 모달만 반복 안내).

## 리스크
- 멀티커플 스코핑: `daily_notes` 의 모든 쿼리에 `couple_id=?` 필수(테넌트 격리 테스트로 검증).
- 프론트 a/b 매핑 오류 → 본인 생일을 파트너 필드에 저장하는 버그 가능. `is_member_a` 를 서버에서 권위있게 내려 방지.

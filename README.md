# 우리만의 공간 — couple.ai-ve.uk

오직 두 사람만 들어올 수 있는 작은 공간.
D-day, 사진, 캘린더, 버킷리스트, 지도, 콕찌르기, 그리고 마스코트 토끼 **코코🐰**의
오늘 코스 추천(Gemini)까지. 완전 단일 Python 서버 + 단일 SPA.

## 빠른 시작 (로컬)

```bash
cd /home/opc/projects/couple
cp .env.example .env       # 값들 채우기
python3.11 -m pip install --user -r requirements.txt
python3.11 -m uvicorn server:app --host 0.0.0.0 --port 8800 --reload
# → http://localhost:8800
```

`.env`에 채워야 하는 값:

| 키 | 설명 |
|---|---|
| `ALLOWED_EMAILS` | 들어올 수 있는 두 이메일 (쉼표로 구분) |
| `SECRET_KEY` | 세션 서명 키 — `openssl rand -hex 32` |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `KAKAO_JS_KEY` | https://developers.kakao.com → 앱 → JavaScript 키 |
| `ANNIVERSARY_DATE` | 만난 날 (YYYY-MM-DD) — 앱 설정에서도 변경 가능 |
| `NICKNAME_A`, `NICKNAME_B` | 두 사람 닉네임 — 앱 설정에서 변경 가능 |
| `SMTP_*` | (선택) 로그인 코드 이메일 발송. 비우면 `journalctl`에 코드 출력 |

## 배포 (cloudflared + systemd 패턴)

이 OCI 박스의 다른 서비스(`arcai-ve`, `arquant`, `paylink` 등)와 동일한 구조다.

```bash
# 1) systemd 서비스 등록
sudo cp /home/opc/projects/couple/deploy/couple.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now couple

# 2) Cloudflare Tunnel
#    - Cloudflare 대시보드 → Zero Trust → Networks → Tunnels → Create
#    - 이름: couple-tunnel, Public Hostname: couple.ai-ve.uk → http://localhost:8800
#    - 'Install connector' 토큰 복사
sudo cp /home/opc/projects/couple/deploy/cloudflared-couple.service /etc/systemd/system/
sudo nano /etc/systemd/system/cloudflared-couple.service   # REPLACE_WITH_TUNNEL_TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-couple

# 3) (선택) 같은 OCI 박스에 nginx 거치고 싶다면
sudo cp /home/opc/projects/couple/deploy/nginx-couple.conf /etc/nginx/conf.d/couple.conf
sudo nginx -t && sudo nginx -s reload    # ⚠️ systemctl reload nginx 는 이 환경에서 깨져 있음
```

## 코드 변경 반영 = systemctl restart

```bash
sudo systemctl restart couple
sudo systemctl status couple
journalctl -u couple -f          # 라이브 로그 (로그인 코드도 여기 찍힘)
```

## 기능

- **🏠 홈** — 큰 D-day 카운터(다음 마일스톤·기념일 자동), 마스코트 토끼 클릭 → 코코랑 대화로 점프, 빠른 통계.
- **📷 추억(사진)** — 멀티 업로드, EXIF에서 GPS/촬영시각 자동 추출, 장소·시간 검색, 상세 모달에서 캡션·장소 편집, 다운로드.
- **📅 캘린더** — 월별 그리드 + 일별 일정 리스트, 색상·시간·메모·리마인더(서버가 30초 단위로 폴링해 WebSocket 푸시).
- **✨ 버킷리스트** — 이모지 아이콘 + 목표일, 탭으로 완료. 둘 중 한 명이 추가/완료하면 상대에게 실시간 알림.
- **🗺️ 지도** — 카카오 지도 SDK, 다녀온/가볼 곳 필터, 우클릭(또는 long-press)으로 핀 추가.
- **💬 코코 챗봇** — Gemini API. 시간대 맞춤 인삿말, 시간대 기반 제안 칩(아침/점심/저녁/심야), 대화 기록 보존.
- **콕찌르기** — 미리 정의된 10가지 이모지(+커스텀 메시지), WebSocket으로 즉시 푸시. 브라우저 알림 + 진동.
- **테마/마스코트** — 5가지 파스텔 테마(rosy/mint/butter/lavender/sky), 마스코트 종류.
- **2인 전용 인증** — `.env`의 두 이메일만 통과, 6자리 코드 매직링크 90일 세션.

## 함정 (Gotchas)

- 테스트는 `python3.11`. 시스템 기본 `python`은 deps 부재로 실패.
- `data/`와 `uploads/`는 gitignore. 사진/DB는 백업 따로 관리.
- nginx 리로드는 **`nginx -s reload`** 만 동작. `systemctl reload nginx`는 이 머신에서 깨져 있음.
- 자동 백업 커밋이 주기적으로 `git add -A` 한다. 시크릿이 들어가지 않게 `.gitignore` 확실히.
- SMTP 미설정 시 로그인 코드는 `journalctl -u couple` 에서 확인. 처음 로그인할 때 이걸로 들어가면 됨.

## 파일 트리

```
couple/
├── server.py                 # FastAPI 진입점 + WebSocket + 리마인더 루프
├── requirements.txt
├── .env.example
├── app/
│   ├── config.py             # env 로딩
│   ├── db.py                 # SQLite 스키마 + cursor() 컨텍스트
│   ├── auth.py               # 이메일 화이트리스트 + 코드 + 쿠키
│   ├── realtime.py           # 이메일별 WebSocket Hub
│   └── routes/
│       ├── auth_routes.py
│       ├── settings_routes.py    (+ /dday)
│       ├── photos_routes.py      (EXIF 파싱 포함)
│       ├── calendar_routes.py    (자동 done 처리)
│       ├── bucket_routes.py
│       ├── places_routes.py
│       ├── chat_routes.py        (Gemini)
│       └── poke_routes.py
├── templates/
│   ├── login.html            # 매직코드 로그인
│   ├── app.html              # 메인 SPA
│   └── _mascot_bunny.svg     # 인라인 마스코트
├── static/
│   ├── css/app.css           # 디자인 시스템
│   ├── js/app.js             # Alpine 컴포넌트
│   ├── js/alpine.min.js      # 로컬 호스팅
│   └── manifest.webmanifest  # PWA
├── data/couple.db            # (gitignored) SQLite
├── uploads/photos/           # (gitignored) 사진 파일
└── deploy/
    ├── couple.service
    ├── cloudflared-couple.service
    └── nginx-couple.conf
```

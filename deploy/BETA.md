# 베타 배포 절차 (beta.couple.ai-ve.uk)

> 모든 단계는 사장님이 실행. 코드/AI 는 실행하지 않는다. 운영(main, 8800)은 무영향.

## 1. 별도 워크트리 (코드 격리 — 운영 디렉터리와 분리)
    cd /home/opc/projects/couple
    git worktree add /home/opc/projects/couple-beta beta

## 2. 베타 DB 시드 (운영 DB 는 읽기전용 복제)
    cd /home/opc/projects/couple-beta
    python3.11 scripts/seed_beta_db.py        # data/couple_beta.db 생성
    # couple #1(현 ALLOWED_EMAILS 2명) 부트스트랩은 첫 기동 시 _migrate() 가 자동 수행

## 3. systemd 등록 (port 8801)
    sudo cp deploy/couple-beta.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now couple-beta.service
    systemctl status couple-beta.service       # 127.0.0.1:8801 확인

## 4. Cloudflare 대시보드 (Zero Trust → Networks → Tunnels → couple-tunnel)
- Public Hostname 추가: `beta.couple.ai-ve.uk` → `http://127.0.0.1:8801`
- 그 호스트의 **Access 정책 해제**(오픈 가입 테스트). `/ws` 도 같은 서비스라 별도 설정 불필요.

## 5. 동작 확인
- https://beta.couple.ai-ve.uk → login → 신규 이메일로 코드 로그인
  (SMTP 미설정이면 코드는 `journalctl -u couple-beta` 로그에 출력)
- 두 계정으로 초대 → 수락 → 데이터 격리/커플 해제/카카오 가져오기 확인.

## 카카오 가져오기 (검증 완료 2026-06-12)
- 동작: 공유 링크 → folderid 추출 → `map.kakao.com/favorite/list?folderid=...`(Referer 필수) → 장소 파싱.
- 비공식 내부 엔드포인트라 카카오가 바꾸면 깨질 수 있음 — 깨지면 `app/kakao_import.py` 의
  `fetch_folder_places`/`parse_favorites` 만 조정.
- SSRF 방어로 카카오 호스트(kko.to / *.kakao.com)만 허용.

## 롤백
    sudo systemctl disable --now couple-beta.service
    git worktree remove /home/opc/projects/couple-beta

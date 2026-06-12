# 베타 배포 (beta.couple.ai-ve.uk)

> 상태: **배포 완료(2026-06-12)**. 남은 건 4번(Cloudflare 호스트 추가, 사장님만 가능)뿐.
> 운영(couple.ai-ve.uk, 8800)은 멀티커플 코드로 정식 채택됨. 베타(8801)는 오픈가입 테스트용.

## 실제 구성 (적용됨)

- `beta` → `main` 머지 완료. 운영·베타 **같은 코드(같은 디렉터리 `/home/opc/projects/couple`)** 를
  공유하고 **환경변수로만** 분리한다. (worktree 안 씀.)
- 운영 `couple.service` (8800): 기본 DB `data/couple.db`, 화이트리스트(OPEN_SIGNUP 미설정).
- 베타 `couple-beta.service` (8801): `COUPLE_DB=data/couple_beta.db`, `SESSION_COOKIE=couple_beta_session`,
  `OPEN_SIGNUP=1`. 부팅 시 자동 기동(enabled).
- 베타 DB `data/couple_beta.db` 는 운영 DB(정리본)의 복제 — couple #1(현호/숙영) 이관됨, 신규 가입은 couple #2+.
- 운영 DB 정리 전 백업: `data/couple.db.bak-pre-cleanup-20260612` (문제 시 복원용).

### 1~3. (완료됨) 코드/DB/서비스
이미 적용됨. 재구성이 필요할 때만:
```bash
# 베타 DB 재시드(운영 DB 읽기전용 일관 복제). 기존 파일 있으면 먼저 삭제.
python3.11 - <<'PY'
import sqlite3
src = sqlite3.connect('file:/home/opc/projects/couple/data/couple.db?mode=ro', uri=True)
dst = sqlite3.connect('/home/opc/projects/couple/data/couple_beta.db')
with dst:
    src.backup(dst)
src.close(); dst.close()
print('re-seeded')
PY
sudo cp deploy/couple-beta.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now couple-beta.service
curl -s http://127.0.0.1:8801/health     # {"ok":true}
```

## 4. Cloudflare 대시보드 — 사장님 액션 (남은 단계)
Zero Trust → Networks → Tunnels → `couple-tunnel`:
- **Public Hostname 추가**: `beta.couple.ai-ve.uk` → `http://127.0.0.1:8801`
- **Access 전부 통과**: `beta.couple.ai-ve.uk` 용 Access 앱을 만들지 않거나(권장),
  기존 앱이 서브도메인까지 덮으면 그 호스트에 **Action=Bypass, Include=Everyone** 정책 추가.
- ⚠️ 운영 도메인(`couple.ai-ve.uk`)의 2-이메일 Access 는 그대로 둔다.

## 5. 동작 확인 (4번 이후)
- https://beta.couple.ai-ve.uk → 신규 이메일로 코드 로그인(SMTP 미설정이면 `journalctl -u couple-beta` 에 코드 출력)
- 두 계정으로 초대 → 수락 → 데이터 격리 / 커플 해제 / 카카오 가져오기 확인.

## 카카오 가져오기 (검증 완료 2026-06-12)
- 공유 링크 → folderid 추출 → `map.kakao.com/favorite/list?folderid=...`(Referer 필수) → 파싱.
- 비공식 내부 엔드포인트라 카카오가 바꾸면 깨질 수 있음 → `app/kakao_import.py` 의
  `fetch_folder_places`/`parse_favorites` 만 조정. SSRF 로 카카오 호스트(kko.to / *.kakao.com)만 허용.

## 롤백
```bash
sudo systemctl disable --now couple-beta.service      # 베타만 내림
# 운영을 멀티커플 이전으로 되돌리려면(비추천): couple.service 정지 →
#   data/couple.db.bak-pre-cleanup-20260612 로 복원 → main 을 49f2c13 으로 reset → 재기동
```

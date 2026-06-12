"""운영 couple.db 를 베타 DB 로 안전 복제(읽기전용). 실행:
    python3.11 scripts/seed_beta_db.py
운영 DB 는 절대 수정하지 않는다. couple #1 부트스트랩은 베타 첫 기동의 _migrate() 가 수행."""
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
    # 읽기전용으로 열어 sqlite backup API 로 WAL 포함 일관 스냅샷 복사
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    dst = sqlite3.connect(str(DST))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    print(f"복제 완료: {DST}")


if __name__ == "__main__":
    main()

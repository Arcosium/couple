#!/usr/bin/env python3
"""기존 사진 일괄 태깅 — 태그가 비어 있는 사진만 로컬 LLM 으로 태깅한다(멱등, 재실행 안전).

    python3 scripts/tag_all_photos.py [--limit N] [--retag]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings                       # noqa: E402
from app.db import cursor, init_db                    # noqa: E402
from app.photo_tagger import tag_photo                # noqa: E402
from app.routes.photos_routes import _ensure_thumb    # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--retag", action="store_true", help="이미 태그가 있는 사진도 다시")
    args = ap.parse_args()

    init_db()   # tags 컬럼 마이그레이션(멱등)
    sql = "SELECT id, filename, couple_id FROM photos"
    if not args.retag:
        sql += " WHERE tags IS NULL OR tags=''"
    sql += " ORDER BY uploaded_at DESC"
    if args.limit:
        sql += f" LIMIT {args.limit}"
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql).fetchall()]
    print(f"대상 {len(rows)}장")

    ok = miss = fail = 0
    for i, r in enumerate(rows, 1):
        src = settings.uploads_dir / r["filename"]
        if not src.exists():
            miss += 1
            continue
        if args.retag:   # 덮어쓰기는 tag_photo 의 '빈 태그만' 가드에 걸리니 먼저 비운다
            with cursor() as cur:
                cur.execute("UPDATE photos SET tags=NULL WHERE id=?", (r["id"],))
        tags = await tag_photo(r["id"], _ensure_thumb(r["id"], src) or src, r["couple_id"])
        ok += bool(tags)
        fail += not tags
        print(f"[{i}/{len(rows)}] {r['id']} → {', '.join(tags) if tags else '(실패)'}", flush=True)
    print(f"\n완료: 성공 {ok} · 실패 {fail} · 파일없음 {miss}")


if __name__ == "__main__":
    asyncio.run(main())

"""관광 스냅샷 로더 — 코코의 tour_search / list_festivals 가 읽는 정적 데이터.

이 앱은 런타임에 TourAPI 를 호출하지 않는다. 하루 1회 bake 크론
(관광데이터_공모전/bake_couple_snapshot.py)이 아래 두 파일을 굽고, 여기서 읽는다.

  data/tour_pois.json       — 관광지·문화시설·여행코스·레포츠 코퍼스(전국 ~2만)
  data/tour_festivals.json  — 앞으로 열리는 축제

파일이 없어도(아직 안 구웠어도) 죽지 않고 빈 결과를 돌려준다.
파일이 갱신되면 mtime 을 보고 자동 재로딩한다(재시작 불필요).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import settings

KST = timezone(timedelta(hours=9))

_POIS_PATH = settings.data_dir / "tour_pois.json"
_FEST_PATH = settings.data_dir / "tour_festivals.json"

# 경로별 (mtime, items) 캐시
_cache: dict[str, tuple[float, list[dict]]] = {}


def _today() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def _load(path) -> list[dict]:
    """mtime 기반 캐시 로드. 파일 없거나 깨지면 []."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            items = json.load(f).get("items", [])
    except (OSError, ValueError):
        items = []
    _cache[str(path)] = (mtime, items)
    return items


def _fmt_date(ymd: str) -> str:
    return f"{ymd[0:4]}.{ymd[4:6]}.{ymd[6:8]}" if len(ymd) == 8 else ymd


def _duration_days(start: str, end: str) -> int | None:
    """축제 기간(일). 파싱 실패 시 None."""
    try:
        s = datetime.strptime(start, "%Y%m%d")
        e = datetime.strptime(end, "%Y%m%d")
        return (e - s).days + 1
    except (ValueError, TypeError):
        return None


def _match(hay: str, needle: str) -> bool:
    return needle in hay


def search_pois(keyword: str = "", area: str = "", kind: str = "", limit: int = 8) -> list[dict]:
    """관광지 코퍼스 검색.
    keyword — 제목/주소 부분일치. area — 광역지역명('서울','제주'…). kind — 관광지/문화시설/여행코스/레포츠.
    제목 선두일치·사진 보유를 우선해 정렬한다."""
    items = _load(_POIS_PATH)
    kw = (keyword or "").strip()
    area = (area or "").strip()
    kind = (kind or "").strip()

    def ok(it: dict) -> bool:
        if area and area not in it.get("area", ""):
            return False
        if kind and kind != it.get("type", ""):
            return False
        if kw and not (_match(it.get("title", ""), kw) or _match(it.get("addr", ""), kw)):
            return False
        return True

    hits = [it for it in items if ok(it)]

    def score(it: dict):
        title = it.get("title", "")
        return (
            0 if kw and title.startswith(kw) else (1 if kw and kw in title else 2),
            0 if it.get("image") else 1,
            0 if (it.get("lat") and it.get("lng")) else 1,
            title,
        )

    hits.sort(key=score)
    return [
        {
            "name": it["title"],
            "type": it.get("type", ""),
            "area": it.get("area", ""),
            "address": it.get("addr", ""),
            "lat": it.get("lat"),
            "lng": it.get("lng"),
            "image": it.get("image"),
        }
        for it in hits[: max(1, min(limit, 20))]
    ]


# 이보다 긴 '축제'는 상시 프로그램(페인터즈·파수의식 등)으로 보고 축제 목록에서 제외
_MAX_FESTIVAL_DAYS = 90


def upcoming_festivals(area: str = "", keyword: str = "", days: int = 0, limit: int = 8) -> list[dict]:
    """앞으로 열리는(또는 진행 중인) 축제.
    area — 광역지역명. keyword — 제목 부분일치. days — 오늘부터 N일 이내 시작하는 것만(0=제한없음).
    상시 프로그램(기간 90일 초과)은 제외. 시작일 오름차순."""
    items = _load(_FEST_PATH)
    today = _today()
    area = (area or "").strip()
    kw = (keyword or "").strip()
    cutoff = None
    if days and days > 0:
        cutoff = (datetime.now(KST) + timedelta(days=days)).strftime("%Y%m%d")

    out = []
    for it in items:
        start, end = it.get("start", ""), it.get("end", "")
        if end and end < today:                       # 이미 끝남
            continue
        dur = _duration_days(start, end)
        if dur is not None and dur > _MAX_FESTIVAL_DAYS:   # 상시 프로그램 → 축제 아님
            continue
        if area and area not in it.get("area", ""):
            continue
        if kw and kw not in it.get("title", ""):
            continue
        if cutoff and start > cutoff:                 # 너무 먼 미래
            continue
        out.append(it)

    out.sort(key=lambda x: (x.get("start", ""), x.get("title", "")))
    return [
        {
            "name": it["title"],
            "area": it.get("area", ""),
            "period": f"{_fmt_date(it.get('start',''))} ~ {_fmt_date(it.get('end',''))}",
            "ongoing": bool(it.get("start", "") <= today <= it.get("end", "")),
            "address": it.get("addr", ""),
            "lat": it.get("lat"),
            "lng": it.get("lng"),
            "image": it.get("image"),
            "tel": it.get("tel"),
        }
        for it in out[: max(1, min(limit, 20))]
    ]


def stats() -> dict:
    return {"pois": len(_load(_POIS_PATH)), "festivals": len(_load(_FEST_PATH))}


# ---------------------------------------------------------------------------- #
# 시맨틱 검색 (additive) — "조용한 바다 근처 축제" 같은 의미 질의. 부분일치가 못 잡는 것.
#   로컬 임베딩(:8765)만 사용 — TourAPI 무호출 원칙 유지. 스냅샷 mtime 키 인덱스(멱등).
#   arcembed 미가용·오류 시 기존 부분일치로 폴백(무영향).
# ---------------------------------------------------------------------------- #
def _arcembed():
    try:
        import arcembed
        return arcembed
    except Exception:
        import os as _os
        import sys as _sys
        lib = _os.path.expanduser("~/projects/lib")
        if lib not in _sys.path:
            _sys.path.insert(0, lib)
        try:
            import arcembed
            return arcembed
        except Exception:
            return None


def _vec_index(path, textfn):
    """스냅샷(path) → 임베딩 인덱스(mtime 접미 파일, 1회 빌드). (ix, items) 또는 None."""
    import glob as _glob
    import os as _os
    ae = _arcembed()
    if ae is None:
        return None
    items = _load(path)
    if not items:
        return None
    try:
        mtime = int(path.stat().st_mtime)
    except OSError:
        return None
    dbp = f"{path}.{mtime}.vec.db"
    ix = ae.VectorIndex(dbp)
    if ix.count() < len(items):
        todo = [(str(i), it) for i, it in enumerate(items) if not ix.has(str(i))]
        for b in range(0, len(todo), 512):
            chunk = todo[b:b + 512]
            V = ae.embed([textfn(it) for _, it in chunk])
            ix.add_many([(cid, V[j], {"i": int(cid)}) for j, (cid, _it) in enumerate(chunk)])
        for old in _glob.glob(f"{path}.*.vec.db"):   # 오래된 스냅샷 인덱스 청소
            if old != dbp:
                try:
                    _os.remove(old)
                except OSError:
                    pass
    return ix, items


def _poi_text(it) -> str:
    return f"{it.get('title','')} {it.get('area','')} {it.get('type','')} {it.get('addr','')}".strip()


def _fest_text(it) -> str:
    return f"{it.get('title','')} {it.get('area','')} {it.get('addr','')}".strip()


def _poi_out(it: dict) -> dict:
    return {"name": it["title"], "type": it.get("type", ""), "area": it.get("area", ""),
            "address": it.get("addr", ""), "lat": it.get("lat"), "lng": it.get("lng"),
            "image": it.get("image")}


def semantic_pois(query: str = "", area: str = "", kind: str = "", limit: int = 8) -> list[dict]:
    """의미 기반 관광지 검색. 실패 시 search_pois 로 폴백."""
    res = _vec_index(_POIS_PATH, _poi_text)
    if not res or not (query or "").strip():
        return search_pois(query, area, kind, limit)
    ix, items = res
    ae = _arcembed()
    out = []
    for _cid, _s, m in ix.search(ae.embed(query), k=max(limit * 5, 25)):
        it = items[m["i"]]
        if area and area not in it.get("area", ""):
            continue
        if kind and kind != it.get("type", ""):
            continue
        out.append(_poi_out(it))
        if len(out) >= max(1, min(limit, 20)):
            break
    return out


def semantic_festivals(query: str = "", area: str = "", days: int = 0, limit: int = 8) -> list[dict]:
    """의미 기반 축제 검색. 종료·상시(90일↑)·기간 필터는 upcoming_festivals 와 동일하게 유지."""
    res = _vec_index(_FEST_PATH, _fest_text)
    if not res or not (query or "").strip():
        return upcoming_festivals(area, query, days, limit)
    ix, items = res
    ae = _arcembed()
    today = _today()
    area = (area or "").strip()
    cutoff = (datetime.now(KST) + timedelta(days=days)).strftime("%Y%m%d") if days and days > 0 else None
    out = []
    for _cid, _s, m in ix.search(ae.embed(query), k=max(limit * 8, 40)):
        it = items[m["i"]]
        start, end = it.get("start", ""), it.get("end", "")
        if end and end < today:
            continue
        dur = _duration_days(start, end)
        if dur is not None and dur > _MAX_FESTIVAL_DAYS:
            continue
        if area and area not in it.get("area", ""):
            continue
        if cutoff and start > cutoff:
            continue
        out.append({"name": it["title"], "area": it.get("area", ""),
                    "period": f"{_fmt_date(start)} ~ {_fmt_date(end)}",
                    "ongoing": bool(start <= today <= end), "address": it.get("addr", ""),
                    "lat": it.get("lat"), "lng": it.get("lng"), "image": it.get("image"),
                    "tel": it.get("tel")})
        if len(out) >= max(1, min(limit, 20)):
            break
    return out

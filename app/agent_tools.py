"""
챗봇이 사용하는 도구(Function Calling) 모음.

- 각 도구는 async 함수이며 첫 인자로 `user_email` 을 받는다(요청자).
- 모든 도구는 dict 를 반환 (성공 시 결과 데이터, 실패 시 `{"error": "..."}`).
- 데이터 변경 도구는 양쪽 클라이언트에 WebSocket 이벤트를 브로드캐스트해서
  코코가 일정/장소/버킷 등을 추가하자마자 두 분 화면 모두 자동 갱신된다.
"""
from __future__ import annotations

import datetime
import inspect
import secrets
import time
from pathlib import Path

import httpx

from .config import settings
from .db import cursor, kv_get, kv_set
from .realtime import hub


# ── 헬퍼 ─────────────────────────────────────────────
async def _broadcast(couple_id: int, kind: str, **extra) -> None:
    from .db import couple_members
    payload = {"kind": kind, **extra}
    for em in couple_members(couple_id):
        await hub.send(em, payload)


def _now_ms() -> str:
    return str(int(time.time() * 1000))


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── 카카오 검색(REST) ───────────────────────────────
async def kakao_search(query: str, **_) -> dict:
    if not settings.kakao_rest_key:
        return {"error": "KAKAO_REST_KEY not configured in .env"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://dapi.kakao.com/v2/local/search/keyword.json",
                params={"query": query, "size": 5},
                headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"},
            )
        r.raise_for_status()
        docs = r.json().get("documents", [])
        return {
            "results": [
                {
                    "name": d["place_name"],
                    "address": d.get("road_address_name") or d.get("address_name") or "",
                    "category": (d.get("category_name") or "").split(" > ")[-1],
                    "lat": float(d["y"]),
                    "lng": float(d["x"]),
                    "phone": d.get("phone") or "",
                }
                for d in docs
            ]
        }
    except Exception as e:
        return {"error": f"kakao_search failed: {e}"}


# ── 관광 스냅샷(한국관광공사 TourAPI, 오프라인) ─────────────
async def tour_search(keyword: str = "", area: str = "", kind: str = "", limit: int = 8, **_) -> dict:
    """전국 관광지·문화시설·여행코스·레포츠 코퍼스(사진·좌표 포함) 오프라인 검색.
    의미검색은 opt-in(COUPLE_SEMANTIC=1) — POI ~2만건 첫 색인 지연 때문(임베딩은 bake 권장)."""
    import os
    from . import tour_data
    try:
        if os.getenv("COUPLE_SEMANTIC", "0") == "1" and (keyword or "").strip():
            results = tour_data.semantic_pois(query=keyword, area=area, kind=kind, limit=limit)
        else:
            results = tour_data.search_pois(keyword=keyword, area=area, kind=kind, limit=limit)
    except Exception:
        results = tour_data.search_pois(keyword=keyword, area=area, kind=kind, limit=limit)
    if not results:
        return {"results": [], "note": "관광 코퍼스에 매칭 없음(다른 지역/키워드로 다시 시도하거나 kakao_search 사용)"}
    return {"results": results}


async def list_festivals(area: str = "", keyword: str = "", days: int = 0, limit: int = 8, **_) -> dict:
    """앞으로 열리는(또는 진행 중인) 축제. days=7 이면 이번 주 이내.
    keyword 가 자연어면 의미검색으로 찾는다('바다 근처 조용한 축제'). 실패 시 부분일치 폴백."""
    from . import tour_data
    try:
        festivals = tour_data.semantic_festivals(query=keyword, area=area, days=days, limit=limit)
    except Exception:
        festivals = tour_data.upcoming_festivals(area=area, keyword=keyword, days=days, limit=limit)
    if not festivals:
        return {"festivals": [], "note": "해당 조건의 예정 축제 없음"}
    return {"festivals": festivals}


# ── PLACES ───────────────────────────────────────────
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
             _now_iso(), user_email, couple_id),
        )
    await _broadcast(couple_id, "place_added", name=name, place_kind=kind, by=user_email)
    return {"id": pid, "name": name, "kind": kind, "ok": True}


async def list_places(couple_id: int, kind: str = "", query: str = "", **_) -> dict:
    sql = "SELECT id, name, address, lat, lng, kind, category, memo FROM places WHERE couple_id=?"
    args: list = [couple_id]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if query:
        sql += " AND (name LIKE ? OR address LIKE ? OR memo LIKE ?)"
        args.extend([f"%{query}%"] * 3)
    sql += " ORDER BY created_at DESC LIMIT 50"
    with cursor() as cur:
        return {"items": [dict(r) for r in cur.execute(sql, args).fetchall()]}


async def update_place(user_email: str, couple_id: int, place_id: str, **kw) -> dict:
    fields, args = [], []
    for k in ("name", "kind", "lat", "lng", "address", "category", "memo"):
        if k in kw and kw[k] is not None:
            fields.append(f"{k}=?")
            args.append(kw[k])
    if not fields:
        return {"error": "no fields to update"}
    args.extend([place_id, couple_id])
    with cursor() as cur:
        cur.execute(f"UPDATE places SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    await _broadcast(couple_id, "place_updated", by=user_email)
    return {"ok": True}


async def delete_place(user_email: str, couple_id: int, place_id: str, **_) -> dict:
    with cursor() as cur:
        row = cur.execute("SELECT name FROM places WHERE id=? AND couple_id=?",
                          (place_id, couple_id)).fetchone()
        if not row:
            return {"error": "place not found"}
        cur.execute("DELETE FROM places WHERE id=? AND couple_id=?", (place_id, couple_id))
    await _broadcast(couple_id, "place_deleted", by=user_email, name=row["name"])
    return {"ok": True, "name": row["name"]}


# ── EVENTS ───────────────────────────────────────────
async def add_event(user_email: str, couple_id: int, title: str, due: str = "", time: str = "",
                    color: str = "", reminder_minutes: int | None = None,
                    note: str = "", end_date: str = "", **_) -> dict:
    eid = _now_ms()
    # 종료일이 시작일 이하면 하루짜리로 취급(end_date=NULL).
    end = end_date or None
    if end and due and end <= due:
        end = None
    with cursor() as cur:
        cur.execute(
            "INSERT INTO events (id, title, due, end_date, time, note, color, source, done, "
            "reminder_minutes, owner_email, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'calendar', 0, ?, ?, ?, ?)",
            (eid, title, due or None, end, time or None, note or None,
             color or "#ec4899", reminder_minutes, user_email,
             datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), couple_id),
        )
    await _broadcast(couple_id, "event_added", title=title, due=due, by=user_email)
    return {"id": eid, "title": title, "ok": True}


async def list_events(couple_id: int, due: str = "", **_) -> dict:
    sql = ("SELECT id, title, due, end_date, time, color, done, reminder_minutes, note "
           "FROM events WHERE couple_id=?")
    args: list = [couple_id]
    if due:
        sql += " AND due=?"
        args.append(due)
    sql += " ORDER BY due IS NULL, due, time LIMIT 50"
    with cursor() as cur:
        return {"items": [dict(r) for r in cur.execute(sql, args).fetchall()]}


async def update_event(user_email: str, couple_id: int, event_id: str, **kw) -> dict:
    fields, args = [], []
    for k in ("title", "due", "end_date", "time", "note", "color", "reminder_minutes"):
        if k in kw and kw[k] is not None:
            fields.append(f"{k}=?")
            args.append(kw[k])
    if "done" in kw and kw["done"] is not None:
        fields.append("done=?")
        args.append(1 if kw["done"] else 0)
    if not fields:
        return {"error": "no fields to update"}
    args.extend([event_id, couple_id])
    with cursor() as cur:
        cur.execute(f"UPDATE events SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    if kw.get("done"):
        await _broadcast(couple_id, "event_done", by=user_email)
    return {"ok": True}


async def delete_event(user_email: str, couple_id: int, event_id: str, **_) -> dict:
    with cursor() as cur:
        cur.execute("DELETE FROM events WHERE id=? AND couple_id=?", (event_id, couple_id))
    return {"ok": True}


# ── BUCKET ───────────────────────────────────────────
async def add_bucket(user_email: str, couple_id: int, title: str, description: str = "",
                     icon: str = "💖", target_date: str = "", **_) -> dict:
    bid = _now_ms()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO bucket (id, title, description, icon, target_date, priority, "
            "done, created_at, owner_email, couple_id) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)",
            (bid, title, description or None, icon or "💖",
             target_date or None, _now_iso(), user_email, couple_id),
        )
    await _broadcast(couple_id, "bucket_added", title=title, by=user_email)
    return {"id": bid, "title": title, "ok": True}


async def list_bucket(couple_id: int, **_) -> dict:
    with cursor() as cur:
        return {"items": [dict(r) for r in cur.execute(
            "SELECT id, title, description, icon, target_date, done FROM bucket "
            "WHERE couple_id=? ORDER BY done, created_at DESC LIMIT 50",
            (couple_id,),
        ).fetchall()]}


async def update_bucket(user_email: str, couple_id: int, bucket_id: str, **kw) -> dict:
    fields, args = [], []
    for k in ("title", "description", "icon", "target_date"):
        if k in kw and kw[k] is not None:
            fields.append(f"{k}=?")
            args.append(kw[k])
    if "done" in kw and kw["done"] is not None:
        fields.append("done=?")
        args.append(1 if kw["done"] else 0)
        if kw["done"]:
            fields.append("done_at=?")
            args.append(_now_iso())
    if not fields:
        return {"error": "no fields to update"}
    args.extend([bucket_id, couple_id])
    with cursor() as cur:
        cur.execute(f"UPDATE bucket SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    if kw.get("done"):
        await _broadcast(couple_id, "bucket_done", by=user_email)
    return {"ok": True}


async def delete_bucket(user_email: str, couple_id: int, bucket_id: str, **_) -> dict:
    with cursor() as cur:
        cur.execute("DELETE FROM bucket WHERE id=? AND couple_id=?", (bucket_id, couple_id))
    return {"ok": True}


# ── PHOTOS ───────────────────────────────────────────
async def list_photos(couple_id: int, query: str = "", place: str = "", **_) -> dict:
    sql = ("SELECT id, caption, place_name, taken_at, lat, lng "
           "FROM photos WHERE couple_id=?")
    args: list = [couple_id]
    if query:
        sql += " AND (caption LIKE ? OR place_name LIKE ?)"
        args.extend([f"%{query}%"] * 2)
    if place:
        sql += " AND place_name=?"
        args.append(place)
    sql += " ORDER BY taken_at DESC LIMIT 50"
    with cursor() as cur:
        return {"items": [dict(r) for r in cur.execute(sql, args).fetchall()]}


async def update_photo(user_email: str, couple_id: int, photo_id: str, **kw) -> dict:
    fields, args = [], []
    for k in ("caption", "place_name", "lat", "lng"):
        if k in kw and kw[k] is not None:
            fields.append(f"{k}=?")
            args.append(kw[k])
    if not fields:
        return {"error": "no fields to update"}
    args.extend([photo_id, couple_id])
    with cursor() as cur:
        cur.execute(f"UPDATE photos SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    return {"ok": True}


async def delete_photo(user_email: str, couple_id: int, photo_id: str, **_) -> dict:
    with cursor() as cur:
        row = cur.execute("SELECT filename FROM photos WHERE id=? AND couple_id=?",
                          (photo_id, couple_id)).fetchone()
        if not row:
            return {"error": "photo not found"}
        cur.execute("DELETE FROM photos WHERE id=? AND couple_id=?", (photo_id, couple_id))
    try:
        (settings.uploads_dir / row["filename"]).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}


# ── MISC ─────────────────────────────────────────────
async def send_poke(user_email: str, couple_id: int, emoji: str, message: str = "", **_) -> dict:
    from .auth import partner_of
    partner = partner_of(user_email)
    if not partner:
        return {"error": "no partner configured"}
    pid = secrets.token_urlsafe(8)
    created = _now_iso()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO pokes (id, from_email, to_email, emoji, message, seen, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (pid, user_email, partner, emoji, message or None, created, couple_id),
        )
    await hub.send(partner, {
        "kind": "poke", "id": pid, "emoji": emoji,
        "message": message or "", "from": user_email, "created_at": created,
    })
    return {"ok": True}


async def get_settings(couple_id: int, **_) -> dict:
    return {
        "anniversary_date": kv_get(couple_id, "anniversary_date", settings.anniversary_date),
        "nickname_a": kv_get(couple_id, "nickname_a", settings.nickname_a),
        "nickname_b": kv_get(couple_id, "nickname_b", settings.nickname_b),
        "theme": kv_get(couple_id, "theme", "rosy"),
        "mascot": kv_get(couple_id, "mascot", "bunny"),
    }


async def update_settings(user_email: str, couple_id: int, **kw) -> dict:
    valid = {"anniversary_date", "nickname_a", "nickname_b", "theme", "mascot"}
    for k, v in kw.items():
        if k in valid and v is not None and str(v).strip():
            kv_set(couple_id, k, str(v).strip())
    return {"ok": True}


# ── DISPATCH ─────────────────────────────────────────
TOOL_DISPATCH = {
    "kakao_search": kakao_search,
    "tour_search": tour_search,
    "list_festivals": list_festivals,
    "add_place": add_place,
    "list_places": list_places,
    "update_place": update_place,
    "delete_place": delete_place,
    "add_event": add_event,
    "list_events": list_events,
    "update_event": update_event,
    "delete_event": delete_event,
    "add_bucket": add_bucket,
    "list_bucket": list_bucket,
    "update_bucket": update_bucket,
    "delete_bucket": delete_bucket,
    "list_photos": list_photos,
    "update_photo": update_photo,
    "delete_photo": delete_photo,
    "send_poke": send_poke,
    "get_settings": get_settings,
    "update_settings": update_settings,
}


async def execute_tool(name: str, args: dict, user_email: str, couple_id: int) -> dict:
    fn = TOOL_DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    # 신뢰된 actor(user_email)와 테넌트(couple_id)는 서버가 세션에서 주입한다.
    # 모델/도구 인자가 이를 덮어써 다른 사람/다른 커플 명의로 행동하는 것을 막기 위해
    # 호출자가 보낸 user_email·couple_id 는 무조건 제거한다.
    args = {k: v for k, v in (args or {}).items() if k not in ("user_email", "couple_id")}
    try:
        # 도구가 실제로 선언한 인자만 주입(read-only 도구는 user_email 안 받음).
        params = inspect.signature(fn).parameters
        if "user_email" in params:
            args["user_email"] = user_email
        if "couple_id" in params:
            args["couple_id"] = couple_id
        return await fn(**args)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── 도구 선언(OpenAI 호환 function-calling 스키마) ────────────────
TOOL_DECLARATIONS = [{
    "function_declarations": [
        {
            "name": "kakao_search",
            "description": "Search Kakao Local by Korean keyword. Returns up to 5 matches with name, address, category, and coordinates. Call this FIRST when the user mentions a real place name so you can get its lat/lng before calling add_place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Korean keyword, e.g. '홍대 정스버거', '제주 흑돼지'"}
                },
                "required": ["query"],
            },
        },
        {
            "name": "tour_search",
            "description": "Search Korea's official tourism corpus (한국관광공사 TourAPI, offline snapshot) for 관광지/문화시설/여행코스/레포츠 nationwide — each with photo, area, and coordinates. Prefer this over kakao_search for TRIP/sightseeing destinations ('제주 가볼 만한 곳', '강릉 여행', '경주 관광지', '실내 데이트 문화시설'). Use kakao_search instead for a specific local shop/cafe/restaurant name. Results include lat/lng so you can add_place directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "제목/주소 부분일치. 예: '해수욕장', '미술관', '경복궁'"},
                    "area": {"type": "string", "description": "광역지역명으로 좁히기. 예: '서울','경기','제주','강원','부산'"},
                    "kind": {"type": "string", "enum": ["관광지", "문화시설", "여행코스", "레포츠"]},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "list_festivals",
            "description": "List upcoming (or currently ongoing) festivals from the official tourism snapshot, each with period·area·photo·coordinates. Use for '이번 주말 뭐하지', '근처에 축제 있어?', or to weave a seasonal festival into a date course. Pass days=7 for this week, area to localize (e.g. '서울','경기'). After suggesting, you can add_event for the date and add_place(kind='wishlist') for the spot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "광역지역명. 예: '서울','경기','강원'"},
                    "keyword": {"type": "string", "description": "축제명 부분일치. 예: '불꽃','벚꽃','커피'"},
                    "days": {"type": "integer", "description": "오늘부터 N일 이내 시작하는 축제만. 이번 주=7, 이번 달=30. 0=제한없음"},
                    "limit": {"type": "integer"},
                },
            },
        },
        {
            "name": "add_place",
            "description": "Add a place to the couple's map. kind='wishlist' (가볼곳) for places they want to visit, 'visited' (가본곳) for places they've already been, 'revisit' (또갈곳) for places they loved and want to go back to. You MUST provide lat/lng — get them from kakao_search first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["visited", "wishlist", "revisit"]},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "address": {"type": "string"},
                    "category": {"type": "string"},
                    "memo": {"type": "string"},
                },
                "required": ["name", "kind", "lat", "lng"],
            },
        },
        {
            "name": "list_places",
            "description": "List saved places. Filter by kind ('wishlist'=가볼곳 / 'revisit'=또갈곳 / 'visited'=가본곳) and/or query (substring on name/address/memo). Use this to find IDs before update_place/delete_place, and when planning a date course (prefer revisit, then wishlist).",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["visited", "wishlist", "revisit"]},
                    "query": {"type": "string"},
                },
            },
        },
        {
            "name": "update_place",
            "description": "Update place fields by id. Always find the id with list_places first if you don't know it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "place_id": {"type": "string"},
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["visited", "wishlist", "revisit"]},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                    "address": {"type": "string"},
                    "category": {"type": "string"},
                    "memo": {"type": "string"},
                },
                "required": ["place_id"],
            },
        },
        {
            "name": "delete_place",
            "description": "Delete a place by id. Confirm with user first if you're about to delete multiple items.",
            "parameters": {
                "type": "object",
                "properties": {"place_id": {"type": "string"}},
                "required": ["place_id"],
            },
        },
        {
            "name": "add_event",
            "description": "Add a calendar event/todo. Date format YYYY-MM-DD, time HH:MM (24h). end_date (YYYY-MM-DD) for multi-day events that span until that date (omit for single-day). reminder_minutes is how many minutes before to ping (10, 30, 60, 1440). color hex like #ec4899.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "end_date": {"type": "string"},
                    "time": {"type": "string"},
                    "note": {"type": "string"},
                    "color": {"type": "string"},
                    "reminder_minutes": {"type": "integer"},
                },
                "required": ["title"],
            },
        },
        {
            "name": "list_events",
            "description": "List events. Optional 'due' (YYYY-MM-DD) to filter by date. Use to find IDs.",
            "parameters": {
                "type": "object",
                "properties": {"due": {"type": "string"}},
            },
        },
        {
            "name": "update_event",
            "description": "Update event by id; pass done=true to mark complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "end_date": {"type": "string"},
                    "time": {"type": "string"},
                    "note": {"type": "string"},
                    "color": {"type": "string"},
                    "reminder_minutes": {"type": "integer"},
                    "done": {"type": "boolean"},
                },
                "required": ["event_id"],
            },
        },
        {
            "name": "delete_event",
            "description": "Delete event by id.",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        },
        {
            "name": "add_bucket",
            "description": "Add a bucket-list item the couple wants to do together. icon is an emoji like '🏝️' or '💖'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "icon": {"type": "string"},
                    "target_date": {"type": "string"},
                },
                "required": ["title"],
            },
        },
        {
            "name": "list_bucket",
            "description": "List bucket items.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "update_bucket",
            "description": "Update bucket item by id; pass done=true to mark accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bucket_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "icon": {"type": "string"},
                    "target_date": {"type": "string"},
                    "done": {"type": "boolean"},
                },
                "required": ["bucket_id"],
            },
        },
        {
            "name": "delete_bucket",
            "description": "Delete bucket item by id.",
            "parameters": {
                "type": "object",
                "properties": {"bucket_id": {"type": "string"}},
                "required": ["bucket_id"],
            },
        },
        {
            "name": "list_photos",
            "description": "List photos. Filter by query (caption/place text) or exact place name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "place": {"type": "string"},
                },
            },
        },
        {
            "name": "update_photo",
            "description": "Update photo metadata (caption, place_name, lat, lng).",
            "parameters": {
                "type": "object",
                "properties": {
                    "photo_id": {"type": "string"},
                    "caption": {"type": "string"},
                    "place_name": {"type": "string"},
                    "lat": {"type": "number"},
                    "lng": {"type": "number"},
                },
                "required": ["photo_id"],
            },
        },
        {
            "name": "delete_photo",
            "description": "Delete a photo by id (file and DB row).",
            "parameters": {
                "type": "object",
                "properties": {"photo_id": {"type": "string"}},
                "required": ["photo_id"],
            },
        },
        {
            "name": "send_poke",
            "description": "Send a cute poke notification to the partner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "emoji": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["emoji"],
            },
        },
        {
            "name": "get_settings",
            "description": "Get current app settings (anniversary, nicknames, theme, mascot).",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "update_settings",
            "description": "Update app settings. anniversary_date YYYY-MM-DD. theme: rosy/mint/butter/lavender/sky. mascot: bunny/cat/bear.",
            "parameters": {
                "type": "object",
                "properties": {
                    "anniversary_date": {"type": "string"},
                    "nickname_a": {"type": "string"},
                    "nickname_b": {"type": "string"},
                    "theme": {"type": "string"},
                    "mascot": {"type": "string"},
                },
            },
        },
    ],
}]

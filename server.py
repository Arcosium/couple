"""
커플 앱 진입점 — FastAPI + Uvicorn.

실행:
    cd /home/opc/projects/couple
    python3.11 -m uvicorn server:app --host 0.0.0.0 --port 8800 --reload

배포는 systemd(`deploy/couple.service`) 가 담당.
"""
import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import read_session, couple_of
from app.config import settings
from app.db import kv_get, couple_members
from app.realtime import hub
from app.routes import (
    auth_routes,
    bucket_routes,
    calendar_routes,
    chat_routes,
    couple_routes,
    notes_routes,
    photos_routes,
    places_routes,
    poke_routes,
    settings_routes,
)

BASE = Path(__file__).resolve().parent

app = FastAPI(title="couple.ai-ve.uk", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://couple.ai-ve.uk", "http://localhost:8800"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# 라우터 등록
app.include_router(auth_routes.router)
app.include_router(couple_routes.router)
app.include_router(settings_routes.router)
app.include_router(photos_routes.router)
app.include_router(calendar_routes.router)
app.include_router(bucket_routes.router)
app.include_router(places_routes.router)
app.include_router(poke_routes.router)
app.include_router(chat_routes.router)
app.include_router(notes_routes.router)


@app.get("/health")
def health():
    return {"ok": True, "online": hub.online()}


# 안드로이드 앱(APK) 다운로드 — 레포 루트의 CoCo.apk 를 직접 서빙.
# (라우트가 없으면 404 → index() 폴백으로 HTML 이 내려가서 "다운로드가 안 됨")
@app.get("/CoCo.apk")
def download_apk():
    apk = BASE / "CoCo.apk"
    if not apk.exists():
        return JSONResponse({"detail": "apk_not_built"}, status_code=404)
    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename="CoCo.apk",
    )


def _asset_version() -> str:
    """app.js/app.css 의 최신 mtime → 정적 자산 캐시 버스팅용 버전.
    배포(파일 변경) 때마다 값이 바뀌어 브라우저/WebView 가 새 JS·CSS 를 강제로 받는다."""
    try:
        files = [BASE / "static" / "js" / "app.js", BASE / "static" / "css" / "app.css"]
        return str(int(max(f.stat().st_mtime for f in files if f.exists())))
    except Exception:
        return "1"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    email = read_session(request)
    if not email:
        return templates.TemplateResponse(
            request, "login.html",
            {"asset_v": _asset_version()},
            headers={"Cache-Control": "no-cache, must-revalidate"})
    cid = couple_of(email)
    if not cid:
        return templates.TemplateResponse(
            request, "match.html",
            {"email": email, "asset_v": _asset_version()},
            headers={"Cache-Control": "no-cache, must-revalidate"})
    return templates.TemplateResponse(
        request, "app.html",
        {
            "kakao_js_key": settings.kakao_js_key,
            "anniversary": kv_get(cid, "anniversary_date", settings.anniversary_date),
            "nickname_a": kv_get(cid, "nickname_a", settings.nickname_a),
            "nickname_b": kv_get(cid, "nickname_b", settings.nickname_b),
            "mascot": kv_get(cid, "mascot", "bunny"),
            "theme": kv_get(cid, "theme", "rosy"),
            "email": email,
            "partner_configured": True,
            "asset_v": _asset_version(),
        },
        headers={"Cache-Control": "no-cache, must-revalidate"})


ALLOWED_WS_ORIGINS = {
    "https://couple.ai-ve.uk",
    "http://localhost:8800",
    "http://127.0.0.1:8800",
}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    # CSWSH 방지: Origin 화이트리스트 (브라우저는 cross-origin WS 에도 쿠키를 동봉한다).
    origin = ws.headers.get("origin", "")
    if origin and origin not in ALLOWED_WS_ORIGINS:
        await ws.close(code=4403)
        return
    # FastAPI WebSocket 은 자동 Depends 가 약하니 쿠키에서 직접 세션 확인
    cookies = {c.split("=", 1)[0]: c.split("=", 1)[1] for c in
               (ws.headers.get("cookie") or "").split("; ") if "=" in c}
    # read_session 은 서명 세션 쿠키만 본다(헤더 폴백 제거). 쿠키 파싱용으로 구성.
    fake_request = type("R", (), {"cookies": cookies, "headers": ws.headers})()
    email = read_session(fake_request)
    if not email:
        await ws.close(code=4401)
        return
    await hub.connect(email, ws)
    try:
        # 연결 유지용 ping
        await ws.send_json({"kind": "hello", "you": email, "ts": int(time.time())})
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(email, ws)


# 캘린더 리마인더 — 백그라운드 루프
async def _reminder_loop():
    """이벤트의 reminder_minutes 가 다가오면 양쪽에 푸시.
    하루에 한 번 다가오는 기념일·생일·마일스톤도 캘린더에 자동 생성한다."""
    from datetime import datetime, timedelta
    from app.db import cursor as db_cursor
    from app.special_events import ensure_special_events

    last_ensure_day = None
    while True:
        try:
            now = datetime.now()
            # 날짜가 바뀌면(또는 첫 루프) 모든 커플의 특별일 자동 기록 갱신
            if last_ensure_day != now.date():
                try:
                    with db_cursor() as cur:
                        cids = [row["id"] for row in
                                cur.execute("SELECT id FROM couples").fetchall()]
                    for cid in cids:
                        ensure_special_events(cid, now.date())
                    last_ensure_day = now.date()
                except Exception as exc:
                    print(f"[special_events] error: {exc}")
            with db_cursor() as cur:
                rows = [dict(r) for r in cur.execute(
                    "SELECT * FROM events WHERE done=0 AND reminder_minutes IS NOT NULL "
                    "AND (notified_ts IS NULL OR notified_ts = 0)"
                ).fetchall()]
            for r in rows:
                if not r.get("due"):
                    continue
                tm = r.get("time") or "09:00"
                try:
                    when = datetime.strptime(f"{r['due']} {tm}", "%Y-%m-%d %H:%M")
                except Exception:
                    continue
                trigger = when - timedelta(minutes=int(r["reminder_minutes"]))
                if now >= trigger and now <= when + timedelta(minutes=5):
                    payload = {
                        "kind": "reminder",
                        "title": r["title"],
                        "due": r["due"],
                        "time": tm,
                        "minutes_to": max(0, int((when - now).total_seconds() / 60)),
                    }
                    for em in couple_members(r["couple_id"]):
                        await hub.send(em, payload)
                    with db_cursor() as cur:
                        cur.execute(
                            "UPDATE events SET notified_ts=? WHERE id=?",
                            (time.time(), r["id"]),
                        )
        except Exception as exc:
            print(f"[reminder_loop] error: {exc}")
        await asyncio.sleep(30)


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_reminder_loop())


@app.exception_handler(404)
async def _not_found(req: Request, exc):
    if req.url.path.startswith("/api/"):
        return JSONResponse({"detail": "not_found"}, status_code=404)
    return await index(req)

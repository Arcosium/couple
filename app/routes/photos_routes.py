import io
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from PIL import Image, ImageOps, ExifTags
import piexif

from ..auth import require_couple
from ..config import settings
from ..db import cursor

router = APIRouter(prefix="/api/photos", tags=["photos"])

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}
MAX_BYTES = 25 * 1024 * 1024  # 25MB
MAX_SIDE = 2560               # 원본 저장 시 긴 변 상한 (그 이상은 재인코딩해 용량↓)
THUMB_SIDE = 480              # 추억 탭 그리드용 썸네일
THUMBS_DIR = settings.uploads_dir / "thumbs"
# 사진 id 는 유일하고 파일 내용은 변하지 않는다 → 영구 캐시(재방문 시 재다운로드 없음).
IMG_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}


def _shrink(raw: bytes, side: int, quality: int) -> bytes | None:
    """긴 변을 side 이하로 줄여 JPEG 로 재인코딩. 실패하면 None."""
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)   # 재인코딩하면 EXIF 회전정보가 사라진다
            im.thumbnail((side, side))
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def _thumb_path(pid: str) -> Path:
    return THUMBS_DIR / f"{pid}.jpg"


def _purge(pid: str, filename: str) -> None:
    """원본 + 썸네일을 함께 지운다(삭제 경로가 둘이라 여기 한 곳으로 모은다)."""
    for p in (settings.uploads_dir / filename, _thumb_path(pid)):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def _ensure_thumb(pid: str, src: Path) -> Path | None:
    """썸네일이 없으면 원본에서 만들어 캐시. 기존 사진도 첫 조회 때 자동 생성된다."""
    tp = _thumb_path(pid)
    if tp.exists():
        return tp
    data = _shrink(src.read_bytes(), THUMB_SIDE, 72)
    if not data:
        return None
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    tp.write_bytes(data)
    return tp


def _parse_exif(raw: bytes) -> tuple[str | None, float | None, float | None, int, int]:
    """Return (taken_at_iso, lat, lng, width, height)."""
    taken_at = None
    lat = lng = None
    width = height = 0
    try:
        with Image.open(io.BytesIO(raw)) as im:
            width, height = im.size
            exif = im._getexif() or {}
        for tag, val in exif.items():
            name = ExifTags.TAGS.get(tag, str(tag))
            if name == "DateTimeOriginal":
                try:
                    taken_at = datetime.strptime(val, "%Y:%m:%d %H:%M:%S").isoformat()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        info = piexif.load(raw)
        gps = info.get("GPS", {}) or {}
        if gps:
            def _to_deg(v, ref):
                d, m, s = v
                deg = d[0] / d[1] + (m[0] / m[1]) / 60 + (s[0] / s[1]) / 3600
                if ref in (b"S", b"W"):
                    deg = -deg
                return deg
            if piexif.GPSIFD.GPSLatitude in gps and piexif.GPSIFD.GPSLongitude in gps:
                lat = _to_deg(gps[piexif.GPSIFD.GPSLatitude],
                              gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N"))
                lng = _to_deg(gps[piexif.GPSIFD.GPSLongitude],
                              gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E"))
    except Exception:
        pass
    return taken_at, lat, lng, width, height


@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    place_name: str = Form(""),
    lat: float | None = Form(None),
    lng: float | None = Form(None),
    taken_at: str | None = Form(None),
):
    user, cid = require_couple(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="unsupported_format")
    raw = await file.read()
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="too_large")
    pid = secrets.token_urlsafe(10)

    # EXIF(촬영시각·GPS)는 재인코딩 전 원본에서 먼저 뽑는다.
    exif_taken, exif_lat, exif_lng, w, h = _parse_exif(raw)

    # 긴 변이 MAX_SIDE 를 넘으면 축소 저장(화질보다 로딩 속도 우선). GIF 는 애니메이션 보존.
    if ext != ".gif" and max(w, h) > MAX_SIDE and (small := _shrink(raw, MAX_SIDE, 85)):
        raw, ext = small, ".jpg"
        with Image.open(io.BytesIO(raw)) as im:
            w, h = im.size

    fname = f"{pid}{ext}"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / fname).write_bytes(raw)
    if (thumb := _shrink(raw, THUMB_SIDE, 72)):
        THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        _thumb_path(pid).write_bytes(thumb)

    final_taken = taken_at or exif_taken or datetime.utcnow().isoformat(timespec="seconds")
    final_lat = lat if lat is not None else exif_lat
    final_lng = lng if lng is not None else exif_lng

    with cursor() as cur:
        cur.execute(
            """INSERT INTO photos
               (id, owner_email, filename, caption, place_name, lat, lng, taken_at,
                uploaded_at, width, height, size_bytes, couple_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid, user, fname, caption.strip(), place_name.strip(),
                final_lat, final_lng, final_taken,
                datetime.utcnow().isoformat(timespec="seconds"),
                w, h, len(raw), cid,
            ),
        )
    return {
        "ok": True,
        "id": pid,
        "url": f"/api/photos/file/{pid}",
        "thumb_url": f"/api/photos/file/{pid}?thumb=1",
        "taken_at": final_taken,
        "lat": final_lat,
        "lng": final_lng,
    }


@router.get("")
def list_photos(
    request: Request,
    q: str | None = None,
    place: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
):
    _email, cid = require_couple(request)
    sql = "SELECT * FROM photos WHERE 1=1 AND couple_id=?"
    args: list = [cid]
    if q:
        sql += " AND (caption LIKE ? OR place_name LIKE ?)"
        args.extend([f"%{q}%", f"%{q}%"])
    if place:
        sql += " AND place_name = ?"
        args.append(place)
    if from_date:
        sql += " AND taken_at >= ?"
        args.append(from_date)
    if to_date:
        sql += " AND taken_at <= ?"
        args.append(to_date)
    sql += " ORDER BY taken_at DESC, uploaded_at DESC LIMIT 500"
    with cursor() as cur:
        rows = [dict(r) for r in cur.execute(sql, args).fetchall()]
    for r in rows:
        r["url"] = f"/api/photos/file/{r['id']}"
        r["thumb_url"] = f"/api/photos/file/{r['id']}?thumb=1"
    return rows


@router.get("/places")
def photo_places(request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        rows = cur.execute(
            """SELECT place_name, lat, lng, COUNT(*) AS n, MAX(taken_at) AS last_taken
               FROM photos WHERE place_name IS NOT NULL AND place_name != '' AND couple_id=?
               GROUP BY place_name ORDER BY n DESC""",
            (cid,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/file/{pid}")
def get_file(pid: str, request: Request, thumb: int = 0):
    _email, cid = require_couple(request)
    with cursor() as cur:
        row = cur.execute("SELECT filename FROM photos WHERE id=? AND couple_id=?",
                          (pid, cid)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    fpath = settings.uploads_dir / row["filename"]
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="missing_file")
    if thumb:
        fpath = _ensure_thumb(pid, fpath) or fpath
    return FileResponse(fpath, headers=IMG_CACHE)


@router.patch("/{pid}")
async def edit(pid: str, request: Request):
    _email, cid = require_couple(request)
    body = await request.json()
    fields = []
    args: list = []
    for k in ("caption", "place_name", "lat", "lng", "taken_at"):
        if k in body:
            fields.append(f"{k}=?")
            args.append(body[k])
    if not fields:
        return {"ok": True}
    args.append(pid)
    args.append(cid)
    with cursor() as cur:
        cur.execute(f"UPDATE photos SET {', '.join(fields)} WHERE id=? AND couple_id=?", args)
    return {"ok": True}


@router.post("/bulk_delete")
async def bulk_delete(request: Request):
    """여러 사진을 한 번에 삭제. body: {ids: [...]}"""
    _email, cid = require_couple(request)
    body = await request.json()
    ids = [str(x) for x in (body.get("ids") or [])]
    if not ids:
        return {"ok": True, "deleted": 0}
    placeholders = ",".join("?" * len(ids))
    with cursor() as cur:
        rows = cur.execute(
            f"SELECT id, filename FROM photos WHERE id IN ({placeholders}) AND couple_id=?",
            ids + [cid],
        ).fetchall()
        cur.execute(
            f"DELETE FROM photos WHERE id IN ({placeholders}) AND couple_id=?",
            ids + [cid],
        )
    for r in rows:
        _purge(r["id"], r["filename"])
    return {"ok": True, "deleted": len(rows)}


@router.post("/bulk_place")
async def bulk_place(request: Request):
    """여러 사진의 장소를 한 번에 변경. body: {ids: [...], place_name, lat?, lng?}"""
    _email, cid = require_couple(request)
    body = await request.json()
    ids = [str(x) for x in (body.get("ids") or [])]
    if not ids:
        return {"ok": True, "updated": 0}
    place_name = (body.get("place_name") or "").strip()
    sets = ["place_name=?"]
    args: list = [place_name]
    if "lat" in body and body["lat"] is not None:
        sets.append("lat=?"); args.append(body["lat"])
    if "lng" in body and body["lng"] is not None:
        sets.append("lng=?"); args.append(body["lng"])
    placeholders = ",".join("?" * len(ids))
    with cursor() as cur:
        cur.execute(
            f"UPDATE photos SET {', '.join(sets)} WHERE id IN ({placeholders}) AND couple_id=?",
            args + ids + [cid],
        )
    return {"ok": True, "updated": len(ids)}


@router.delete("/{pid}")
def delete(pid: str, request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        row = cur.execute("SELECT filename FROM photos WHERE id=? AND couple_id=?",
                          (pid, cid)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not_found")
        cur.execute("DELETE FROM photos WHERE id=? AND couple_id=?", (pid, cid))
    _purge(pid, row["filename"])
    return {"ok": True}

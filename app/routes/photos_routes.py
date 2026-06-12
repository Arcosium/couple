import io
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from PIL import Image, ExifTags
import piexif

from ..auth import require_couple
from ..config import settings
from ..db import cursor

router = APIRouter(prefix="/api/photos", tags=["photos"])

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}
MAX_BYTES = 25 * 1024 * 1024  # 25MB


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
    fname = f"{pid}{ext}"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (settings.uploads_dir / fname).write_bytes(raw)

    exif_taken, exif_lat, exif_lng, w, h = _parse_exif(raw)
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
def get_file(pid: str, request: Request):
    _email, cid = require_couple(request)
    with cursor() as cur:
        row = cur.execute("SELECT filename FROM photos WHERE id=? AND couple_id=?",
                          (pid, cid)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    fpath = settings.uploads_dir / row["filename"]
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="missing_file")
    return FileResponse(fpath)


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
            f"SELECT filename FROM photos WHERE id IN ({placeholders}) AND couple_id=?",
            ids + [cid],
        ).fetchall()
        cur.execute(
            f"DELETE FROM photos WHERE id IN ({placeholders}) AND couple_id=?",
            ids + [cid],
        )
    for r in rows:
        try:
            (settings.uploads_dir / r["filename"]).unlink(missing_ok=True)
        except Exception:
            pass
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
    try:
        (settings.uploads_dir / row["filename"]).unlink(missing_ok=True)
    except Exception:
        pass
    return {"ok": True}

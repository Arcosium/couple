"""카카오맵 저장 폴더 공유 링크 → 장소 후보 추출.

실제 흐름(스파이크로 검증, 2026-06-12):
  공유링크(kko.to 단축 포함) → folderid 추출 → map.kakao.com/favorite/list?folderid=...
  (Referer: https://map.kakao.com/ 필수) → favorites[] 파싱.
favorites 항목: display1=이름, display2=주소, lat/lon=WGS84 좌표, memo=메모, type=='PLACE'.

SSRF 방어: 카카오 호스트 화이트리스트(kko.to / *.kakao.com) + 사설IP 거부 + 리다이렉트 재검증.
parse_favorites 는 순수 함수라 픽스처로 단위 테스트한다."""
import ipaddress
import socket
from urllib.parse import urlparse, urljoin, parse_qs

import httpx

_ALLOWED_EXACT = {"kko.to", "kakao.com"}
_ALLOWED_SUFFIX = (".kakao.com",)
_MAX_REDIRECTS = 5
_REFERER = "https://map.kakao.com/"
_FAVORITE_LIST = "https://map.kakao.com/favorite/list?folderid={folderid}"


class UnsafeURLError(ValueError):
    """SSRF 차단: 허용되지 않은(비카카오/내부망/비https) URL."""


class KakaoImportError(ValueError):
    """folderid 미발견 등 가져오기 실패."""


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in _ALLOWED_EXACT or host.endswith(_ALLOWED_SUFFIX)


def _host_resolves_public(host: str) -> bool:
    """host 가 공인 IP 로만 해석되면 True. 사설/루프백/링크로컬/예약 IP 가 하나라도
    섞이면 False (메타데이터 엔드포인트·DNS 리바인딩 방어)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _validate_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme != "https":
        raise UnsafeURLError("url_not_https")
    host = (p.hostname or "").lower().rstrip(".")
    if not _host_allowed(host):
        raise UnsafeURLError("host_not_allowed")
    if not _host_resolves_public(host):
        raise UnsafeURLError("host_not_public")


def _extract_folderid(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    for key in ("folderid", "folderId", "folderID"):
        vals = q.get(key)
        if vals and vals[0].isdigit():
            return vals[0]
    return None


def parse_favorites(data: dict) -> list[dict]:
    """favorite/list 응답 → 장소 후보. type=='PLACE' 만, 이름+근접좌표 중복 제거."""
    out: list[dict] = []
    for it in (data.get("favorites") or []):
        if not isinstance(it, dict) or it.get("type") != "PLACE":
            continue
        name = (it.get("display1") or "").strip()
        lat, lng = it.get("lat"), it.get("lon")
        if not name or lat is None or lng is None:
            continue
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            continue
        out.append({
            "name": name,
            "road_address": (it.get("display2") or "").strip(),
            "lat": lat,
            "lng": lng,
            "memo": (it.get("memo") or "").strip(),
            "kakao_place_id": str(it.get("key") or ""),
        })
    seen, uniq = set(), []
    for it in out:
        k = (it["name"], round(it["lat"], 5), round(it["lng"], 5))
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


async def resolve_folderid(url: str) -> str:
    """공유 링크(kko.to 단축 포함)를 따라가 folderid 추출. 각 홉 SSRF 재검증."""
    _validate_url(url)
    fid = _extract_folderid(url)
    if fid:
        return fid
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        for _ in range(_MAX_REDIRECTS + 1):
            r = await c.get(url)
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location") or "")
                _validate_url(url)
                fid = _extract_folderid(url)
                if fid:
                    return fid
                continue
            break
    raise KakaoImportError("folderid_not_found")


async def fetch_folder_places(url: str) -> list[dict]:
    """공유 링크 → folderid → favorite/list → 장소 후보 리스트. (라우트가 호출)"""
    folderid = await resolve_folderid(url)
    if not folderid.isdigit():
        raise KakaoImportError("bad_folderid")
    api = _FAVORITE_LIST.format(folderid=folderid)
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": _REFERER}) as c:
        r = await c.get(api)
        r.raise_for_status()
        data = r.json()
    return parse_favorites(data)

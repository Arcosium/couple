"""카카오맵 저장 폴더 공유 링크 → 장소 후보 추출.
파싱(parse_kakao_folder)은 순수 함수라 픽스처로 단위 테스트. 네트워크는 fetch_folder.

주의: 카카오 폴더 공유 페이지의 실제 임베디드 JSON 구조는 환경상 미검증이다.
parse_kakao_folder 는 구조에 관대하게 — 임베디드 JSON 트리에서 name+x+y 를 가진
객체를 전부 수집한다. 실제 링크로의 검증(스파이크)은 별도로 필요."""
import json
import re

import httpx

from .config import settings


def parse_kakao_folder(html: str) -> list[dict]:
    """폴더 공유 HTML 에서 장소 목록 추출. 구조가 안 맞으면 []."""
    out: list[dict] = []
    for m in re.finditer(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                         html, re.DOTALL):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for p in _walk_places(data):
            try:
                out.append({
                    "name": p["name"],
                    "road_address": p.get("road_address") or p.get("address") or "",
                    "lat": float(p["y"]),
                    "lng": float(p["x"]),
                    "kakao_place_id": str(p.get("id") or ""),
                })
            except (KeyError, TypeError, ValueError):
                continue
    seen, uniq = set(), []
    for it in out:
        k = (it["name"], round(it["lat"], 5), round(it["lng"], 5))
        if k not in seen:
            seen.add(k); uniq.append(it)
    return uniq


def _walk_places(data) -> list[dict]:
    """JSON 트리에서 'name'+'x'+'y' 를 가진 dict 들을 모은다(구조 변화에 견고)."""
    found = []
    def rec(node):
        if isinstance(node, dict):
            if "name" in node and "x" in node and "y" in node:
                found.append(node)
            for v in node.values():
                rec(v)
        elif isinstance(node, list):
            for v in node:
                rec(v)
    rec(data)
    return found


async def fetch_folder(url: str) -> str:
    """공유 링크(짧은링크 포함) 최종 HTML."""
    async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                                 headers={"User-Agent": "Mozilla/5.0"}) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.text


async def geocode(name: str) -> dict | None:
    """좌표 없는 항목 보강 — Kakao Local 키워드 검색 첫 결과."""
    if not settings.kakao_rest_key:
        return None
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://dapi.kakao.com/v2/local/search/keyword.json",
                        params={"query": name, "size": 1},
                        headers={"Authorization": f"KakaoAK {settings.kakao_rest_key}"})
    if r.status_code != 200:
        return None
    docs = r.json().get("documents", [])
    if not docs:
        return None
    d = docs[0]
    return {"lat": float(d["y"]), "lng": float(d["x"]),
            "road_address": d.get("road_address_name") or d.get("address_name") or ""}

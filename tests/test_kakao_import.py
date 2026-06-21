import json, os
import pytest
from app import kakao_import

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "kakao_favorites.json")


def test_parse_favorites_extracts_places():
    data = json.load(open(_FIX, encoding="utf-8"))
    items = kakao_import.parse_favorites(data)
    assert len(items) == 2                      # ROUTE 항목은 제외
    a = items[0]
    assert a["name"] == "스타벅스 코엑스몰점"
    assert abs(a["lat"] - 37.5135596) < 1e-6
    assert abs(a["lng"] - 127.0593538) < 1e-6
    assert a["road_address"].startswith("서울 강남구")
    assert items[1]["memo"] == "국물 맛집"


def test_parse_favorites_empty():
    assert kakao_import.parse_favorites({}) == []
    assert kakao_import.parse_favorites({"favorites": []}) == []


def test_extract_folderid_from_long_url():
    assert kakao_import._extract_folderid(
        "https://map.kakao.com/?map_type=TYPE_MAP&folderid=22714483&page=bookmark") == "22714483"
    assert kakao_import._extract_folderid("https://kko.to/abc") is None


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:8500/admin",        # 비https + 내부
    "http://169.254.169.254/latest/meta-data/",  # 메타데이터
    "https://127.0.0.1/",                 # 내부 IP host (allowlist 밖)
    "https://evil.example.com/x",         # 비카카오 호스트
    "https://10.0.0.5/",                  # 사설 IP host
    "ftp://kko.to/x",                     # 비https 스킴
])
def test_validate_url_rejects_unsafe(bad):
    with pytest.raises(kakao_import.UnsafeURLError):
        kakao_import._validate_url(bad)


def test_validate_url_allows_kakao_host_shape():
    # 호스트 화이트리스트 통과(스킴·호스트). DNS 해석은 환경에 따라 다를 수 있으니
    # _host_allowed 단위로 확인.
    assert kakao_import._host_allowed("kko.kakao.com")
    assert kakao_import._host_allowed("place.map.kakao.com")
    assert kakao_import._host_allowed("kko.to")
    assert not kakao_import._host_allowed("evilkakao.com")
    assert not kakao_import._host_allowed("kakao.com.attacker.com")
    assert not kakao_import._host_allowed("127.0.0.1")


from fastapi.testclient import TestClient
from server import app
from app import db, couples
from app.auth import SESSION_COOKIE, make_session_cookie

client = TestClient(app)
def _h(e): return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(e)}"}


def _match(a, b):
    with db.cursor() as cur:
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (em,))
    inv = couples.create_invite(a, b); couples.accept_invite(b, inv["id"])


def test_confirm_inserts_and_skips_dupes():
    _match("k1@t", "k2@t")
    items = [{"name": "샘플카페", "lat": 37.5, "lng": 127.0}]
    r1 = client.post("/api/places/import/kakao/confirm",
                     json={"items": items, "kind": "wishlist"}, headers=_h("k1@t"))
    assert r1.json()["added"] == 1
    r2 = client.post("/api/places/import/kakao/confirm",
                     json={"items": items, "kind": "wishlist"}, headers=_h("k1@t"))
    assert r2.json()["added"] == 0


def test_import_kakao_blocks_ssrf_via_route():
    _match("ssrf1@t", "ssrf2@t")
    r = client.post("/api/places/import/kakao",
                    json={"url": "http://127.0.0.1:8500/"}, headers=_h("ssrf1@t"))
    assert r.status_code == 400
    assert r.json()["detail"] == "bad_url"

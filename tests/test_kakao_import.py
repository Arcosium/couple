import pytest
from app import kakao_import

SAMPLE = '''<html><body>
<script id="data" type="application/json">
{"places":[{"name":"연남동 카페","x":"126.92","y":"37.56","road_address":"서울 마포구 ..."},
           {"name":"망원 칼국수","x":"126.90","y":"37.55","road_address":""}]}
</script></body></html>'''


def test_parse_folder_extracts_places():
    items = kakao_import.parse_kakao_folder(SAMPLE)
    assert len(items) == 2
    assert items[0]["name"] == "연남동 카페"
    assert abs(items[0]["lat"] - 37.56) < 0.01
    assert abs(items[0]["lng"] - 126.92) < 0.01


def test_parse_empty_returns_empty():
    assert kakao_import.parse_kakao_folder("<html></html>") == []


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

client = TestClient(app)
def _h(e): return {"Cf-Access-Authenticated-User-Email": e}


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

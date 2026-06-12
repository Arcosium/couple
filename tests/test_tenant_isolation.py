"""두 커플의 데이터가 서로 안 보여야 한다."""
from fastapi.testclient import TestClient
from server import app
from app import db

client = TestClient(app)


def _match(a, b):
    from app import couples
    with db.cursor() as cur:
        for em in (a, b):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (em,))
    inv = couples.create_invite(a, b)
    couples.accept_invite(b, inv["id"])


def _h(email):
    return {"Cf-Access-Authenticated-User-Email": email}


def test_events_isolated_between_couples():
    _match("iso_a1@t", "iso_a2@t")
    _match("iso_b1@t", "iso_b2@t")
    client.post("/api/events", json={"title": "A커플 일정", "due": "2026-09-01"}, headers=_h("iso_a1@t"))
    b_events = client.get("/api/events", headers=_h("iso_b1@t")).json()
    assert all(e["title"] != "A커플 일정" for e in b_events)


def test_places_isolated_between_couples():
    _match("iso_c1@t", "iso_c2@t")
    _match("iso_d1@t", "iso_d2@t")
    client.post("/api/places", json={"name": "C커플 장소", "lat": 37.5, "lng": 127.0},
                headers=_h("iso_c1@t"))
    d_places = client.get("/api/places", headers=_h("iso_d1@t")).json()
    assert all(p["name"] != "C커플 장소" for p in d_places)


def test_unmatched_data_route_409():
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES ('lone@t', NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL")
    assert client.get("/api/events", headers=_h("lone@t")).status_code == 409


def test_bucket_isolated():
    _match("iso_e1@t", "iso_e2@t"); _match("iso_f1@t", "iso_f2@t")
    client.post("/api/bucket", json={"title": "E버킷"}, headers=_h("iso_e1@t"))
    f = client.get("/api/bucket", headers=_h("iso_f1@t")).json()
    assert all(b["title"] != "E버킷" for b in f)


def test_photos_isolated():
    from app import auth
    _match("iso_g1@t", "iso_g2@t"); _match("iso_h1@t", "iso_h2@t")
    gcid = auth.couple_of("iso_g1@t")
    with db.cursor() as cur:
        cur.execute("INSERT INTO photos (id, owner_email, filename, uploaded_at, couple_id) "
                    "VALUES ('ph1', 'iso_g1@t', 'x.jpg', '2026-01-01', ?)", (gcid,))
    h = client.get("/api/photos", headers=_h("iso_h1@t")).json()
    assert all((p.get("id") if isinstance(p, dict) else None) != "ph1" for p in h)


def test_pokes_isolated():
    _match("iso_i1@t", "iso_i2@t")
    client.post("/api/pokes", json={"emoji": "💗"}, headers=_h("iso_i1@t"))
    _match("iso_j1@t", "iso_j2@t")
    assert client.get("/api/pokes", headers=_h("iso_j1@t")).json() == []

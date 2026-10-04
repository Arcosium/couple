"""오늘 한마디(daily_notes): upsert/삭제/커플격리/mine·partner 구분."""
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


def test_put_then_get_mine():
    _match("n1@t", "n2@t")
    r = client.put("/api/notes", json={"date": "2026-06-12", "content": "오늘 풋살"}, headers=_h("n1@t"))
    assert r.status_code == 200
    g = client.get("/api/notes?date=2026-06-12", headers=_h("n1@t")).json()
    assert g["mine"]["content"] == "오늘 풋살"
    assert g["partner"] is None


def test_upsert_overwrites_same_day():
    _match("n3@t", "n4@t")
    client.put("/api/notes", json={"date": "2026-06-12", "content": "v1"}, headers=_h("n3@t"))
    client.put("/api/notes", json={"date": "2026-06-12", "content": "v2"}, headers=_h("n3@t"))
    g = client.get("/api/notes?date=2026-06-12", headers=_h("n3@t")).json()
    assert g["mine"]["content"] == "v2"
    # 1행만 존재 — couple_of 는 cursor() 블록 밖에서(전역 락 재진입 데드락 방지)
    from app.auth import couple_of
    cid = couple_of("n3@t")
    with db.cursor() as cur:
        n = cur.execute("SELECT COUNT(*) FROM daily_notes WHERE couple_id=? AND author_email='n3@t' AND date='2026-06-12'", (cid,)).fetchone()[0]
    assert n == 1


def test_empty_content_deletes():
    _match("n5@t", "n6@t")
    client.put("/api/notes", json={"date": "2026-06-12", "content": "x"}, headers=_h("n5@t"))
    client.put("/api/notes", json={"date": "2026-06-12", "content": "   "}, headers=_h("n5@t"))
    g = client.get("/api/notes?date=2026-06-12", headers=_h("n5@t")).json()
    assert g["mine"] is None


def test_mine_partner_distinct():
    _match("n7@t", "n8@t")
    client.put("/api/notes", json={"date": "2026-06-12", "content": "내꺼"}, headers=_h("n7@t"))
    client.put("/api/notes", json={"date": "2026-06-12", "content": "상대꺼"}, headers=_h("n8@t"))
    g = client.get("/api/notes?date=2026-06-12", headers=_h("n7@t")).json()
    assert g["mine"]["content"] == "내꺼" and g["partner"]["content"] == "상대꺼"


def test_couple_isolated():
    _match("n9@t", "n10@t"); _match("n11@t", "n12@t")
    client.put("/api/notes", json={"date": "2026-06-12", "content": "A커플"}, headers=_h("n9@t"))
    g = client.get("/api/notes?date=2026-06-12", headers=_h("n11@t")).json()
    assert g["mine"] is None and g["partner"] is None


def test_all_returns_couple_notes_with_mine_flag():
    _match("n15@t", "n16@t")
    client.put("/api/notes", json={"date": "2026-06-12", "content": "내 한마디"}, headers=_h("n15@t"))
    client.put("/api/notes", json={"date": "2026-06-13", "content": "상대 한마디"}, headers=_h("n16@t"))
    rows = client.get("/api/notes/all", headers=_h("n15@t")).json()
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-06-12"]["content"] == "내 한마디" and by_date["2026-06-12"]["mine"] is True
    assert by_date["2026-06-13"]["content"] == "상대 한마디" and by_date["2026-06-13"]["mine"] is False
    # 커플 격리: 다른 커플은 못 봄
    _match("n17@t", "n18@t")
    other = client.get("/api/notes/all", headers=_h("n17@t")).json()
    assert all(r["date"] not in ("2026-06-12", "2026-06-13") for r in other) or other == []


def test_unmatched_409_and_bad_date_400():
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES ('lone2@t', NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL")
    assert client.get("/api/notes?date=2026-06-12", headers=_h("lone2@t")).status_code == 409
    _match("n13@t", "n14@t")
    assert client.put("/api/notes", json={"date": "nope", "content": "x"}, headers=_h("n13@t")).status_code == 400

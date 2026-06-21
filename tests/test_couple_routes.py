from fastapi.testclient import TestClient
from server import app
from app import db
from app.auth import SESSION_COOKIE, make_session_cookie

client = TestClient(app)


def _login(email):
    return {"Cookie": f"{SESSION_COOKIE}={make_session_cookie(email)}"}


def _unmatch(email):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (email,))


def test_status_unmatched():
    _unmatch("r1@t")
    r = client.get("/api/couple/status", headers=_login("r1@t"))
    assert r.json()["matched"] is False


def test_invite_accept_flow():
    _unmatch("r2@t"); _unmatch("r3@t")
    inv = client.post("/api/couple/invite", json={"email": "r3@t"}, headers=_login("r2@t"))
    assert inv.status_code == 200
    iid = inv.json()["id"]
    inb = client.get("/api/couple/invites", headers=_login("r3@t")).json()
    assert any(i["id"] == iid for i in inb["incoming"])
    acc = client.post(f"/api/couple/invites/{iid}/accept", headers=_login("r3@t"))
    assert acc.status_code == 200
    st = client.get("/api/couple/status", headers=_login("r2@t")).json()
    assert st["matched"] is True and st["partner_email"] == "r3@t"


def test_invite_self_400():
    _unmatch("r4@t")
    r = client.post("/api/couple/invite", json={"email": "r4@t"}, headers=_login("r4@t"))
    assert r.status_code == 400


def test_unlink():
    _unmatch("r5@t"); _unmatch("r6@t")
    iid = client.post("/api/couple/invite", json={"email": "r6@t"},
                      headers=_login("r5@t")).json()["id"]
    client.post(f"/api/couple/invites/{iid}/accept", headers=_login("r6@t"))
    r = client.post("/api/couple/unlink", headers=_login("r5@t"))
    assert r.status_code == 200
    assert client.get("/api/couple/status", headers=_login("r5@t")).json()["matched"] is False

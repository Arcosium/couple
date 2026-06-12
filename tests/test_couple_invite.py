import pytest
from app import couples, auth
from app import db


def _fresh(email):
    with db.cursor() as cur:
        cur.execute("INSERT INTO users (email, couple_id) VALUES (?, NULL) "
                    "ON CONFLICT(email) DO UPDATE SET couple_id=NULL", (email,))


def test_invite_creates_pending():
    _fresh("u1@t"); _fresh("u2@t")
    inv = couples.create_invite("u1@t", "u2@t")
    assert inv["status"] == "pending"
    incoming = couples.list_invites("u2@t")["incoming"]
    assert any(i["inviter_email"] == "u1@t" for i in incoming)


def test_cannot_invite_self():
    _fresh("u3@t")
    with pytest.raises(ValueError):
        couples.create_invite("u3@t", "u3@t")


def test_accept_forms_couple_and_clears_others():
    _fresh("a1@t"); _fresh("a2@t"); _fresh("a3@t")
    couples.create_invite("a3@t", "a2@t")          # 다른 pending
    inv = couples.create_invite("a1@t", "a2@t")
    couples.accept_invite("a2@t", inv["id"])
    assert auth.partner_of("a1@t") == "a2@t"
    assert couples.list_invites("a3@t")["outgoing"] == []


def test_cannot_invite_already_matched():
    _fresh("b1@t"); _fresh("b2@t"); _fresh("b3@t")
    inv = couples.create_invite("b1@t", "b2@t")
    couples.accept_invite("b2@t", inv["id"])
    with pytest.raises(ValueError):
        couples.create_invite("b3@t", "b1@t")


def test_preinvite_unregistered_email_ok():
    _fresh("c1@t")
    inv = couples.create_invite("c1@t", "newcomer@t")
    assert inv["invitee_email"] == "newcomer@t"


def test_decline_and_cancel():
    _fresh("d1@t"); _fresh("d2@t")
    inv = couples.create_invite("d1@t", "d2@t")
    couples.decline_invite("d2@t", inv["id"])
    assert couples.list_invites("d2@t")["incoming"] == []
    inv2 = couples.create_invite("d1@t", "d2@t")
    couples.cancel_invite("d1@t", inv2["id"])
    assert couples.list_invites("d1@t")["outgoing"] == []


def test_unlink_dissolves_couple():
    _fresh("e1@t"); _fresh("e2@t")
    inv = couples.create_invite("e1@t", "e2@t")
    couples.accept_invite("e2@t", inv["id"])
    couples.unlink(auth.couple_of("e1@t"))
    assert auth.couple_of("e1@t") is None
    assert auth.couple_of("e2@t") is None

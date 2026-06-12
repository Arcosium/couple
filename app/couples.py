"""커플 매칭/초대 도메인 로직 (라우트와 분리, 순수 테스트 가능).
실패는 ValueError(detail) 로 던지고, 라우트가 400 으로 변환한다."""
import secrets
from datetime import datetime, timezone

from .auth import couple_of
from .db import cursor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(email: str) -> str:
    return (email or "").lower().strip()


def create_invite(inviter: str, invitee: str) -> dict:
    inviter, invitee = _norm(inviter), _norm(invitee)
    if not invitee or "@" not in invitee:
        raise ValueError("bad_email")
    if inviter == invitee:
        raise ValueError("cannot_invite_self")
    if couple_of(inviter):
        raise ValueError("already_matched")
    if couple_of(invitee):
        raise ValueError("invitee_already_matched")
    with cursor() as cur:
        dup = cur.execute(
            "SELECT 1 FROM couple_invites WHERE inviter_email=? AND invitee_email=? "
            "AND status='pending'", (inviter, invitee)).fetchone()
        if dup:
            raise ValueError("duplicate_invite")
        iid = secrets.token_urlsafe(8)
        cur.execute(
            "INSERT INTO couple_invites (id, inviter_email, invitee_email, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)", (iid, inviter, invitee, _now()))
    return {"id": iid, "inviter_email": inviter, "invitee_email": invitee, "status": "pending"}


def list_invites(email: str) -> dict:
    email = _norm(email)
    with cursor() as cur:
        incoming = [dict(r) for r in cur.execute(
            "SELECT * FROM couple_invites WHERE invitee_email=? AND status='pending' "
            "ORDER BY created_at DESC", (email,)).fetchall()]
        outgoing = [dict(r) for r in cur.execute(
            "SELECT * FROM couple_invites WHERE inviter_email=? AND status='pending' "
            "ORDER BY created_at DESC", (email,)).fetchall()]
    return {"incoming": incoming, "outgoing": outgoing}


def _get_invite(iid: str) -> dict | None:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM couple_invites WHERE id=?", (iid,)).fetchone()
    return dict(row) if row else None


def accept_invite(invitee: str, iid: str) -> dict:
    invitee = _norm(invitee)
    inv = _get_invite(iid)
    if not inv or inv["status"] != "pending" or inv["invitee_email"] != invitee:
        raise ValueError("invite_not_found")
    inviter = inv["inviter_email"]
    if couple_of(inviter):
        raise ValueError("inviter_already_matched")
    if couple_of(invitee):
        raise ValueError("already_matched")
    with cursor() as cur:
        cur.execute("INSERT INTO couples (member_a, member_b, created_at) VALUES (?, ?, ?)",
                    (inviter, invitee, _now()))
        cid = cur.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for em in (inviter, invitee):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, ?) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=excluded.couple_id", (em, cid))
        cur.execute("UPDATE couple_invites SET status='accepted', responded_at=? WHERE id=?",
                    (_now(), iid))
        cur.execute(
            "UPDATE couple_invites SET status='canceled', responded_at=? "
            "WHERE status='pending' AND id!=? AND "
            "(inviter_email IN (?,?) OR invitee_email IN (?,?))",
            (_now(), iid, inviter, invitee, inviter, invitee))
    return {"couple_id": cid, "partner": inviter}


def decline_invite(invitee: str, iid: str) -> None:
    invitee = _norm(invitee)
    inv = _get_invite(iid)
    if not inv or inv["invitee_email"] != invitee:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='declined', responded_at=? WHERE id=?",
                    (_now(), iid))


def cancel_invite(inviter: str, iid: str) -> None:
    inviter = _norm(inviter)
    inv = _get_invite(iid)
    if not inv or inv["inviter_email"] != inviter:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='canceled', responded_at=? WHERE id=?",
                    (_now(), iid))


def unlink(couple_id: int) -> list[str]:
    """커플 해제: couples 행 삭제 + 양쪽 couple_id=NULL. 데이터는 옛 couple_id 로 보존(접근 불가).
    반환: 해제된 멤버 이메일 목록(알림용)."""
    from .db import couple_members
    members = couple_members(couple_id)
    with cursor() as cur:
        cur.execute("UPDATE users SET couple_id=NULL WHERE couple_id=?", (couple_id,))
        cur.execute("DELETE FROM couples WHERE id=?", (couple_id,))
    return members

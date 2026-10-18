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


def _resolve(who: str) -> str | None:
    """초대 대상 → 계정 식별값(email 컬럼). 새 가입자는 아이디, 옛 계정은 아이디·이메일 둘 다."""
    with cursor() as cur:
        row = cur.execute("SELECT email FROM users WHERE username=? OR email=?",
                          (who, who)).fetchone()
    return row["email"] if row else None


def create_invite(inviter: str, invitee: str) -> dict:
    inviter, invitee = _norm(inviter), _norm(invitee)
    if not invitee:
        raise ValueError("user_not_found")
    # 가입된 계정만 초대한다. 미가입 대상을 허용하면 그 아이디를 나중에 가입한 사람이 초대를 받는다.
    invitee = _resolve(invitee)
    if not invitee:
        raise ValueError("user_not_found")
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
    # 화면에는 아이디만 보이게 username 을 붙인다(옛 계정의 이메일 노출 방지).
    sql = ("SELECT ci.*, ui.username AS inviter_username, ue.username AS invitee_username "
           "FROM couple_invites ci LEFT JOIN users ui ON ui.email=ci.inviter_email "
           "LEFT JOIN users ue ON ue.email=ci.invitee_email "
           "WHERE ci.{col}=? AND ci.status='pending' ORDER BY ci.created_at DESC")
    with cursor() as cur:
        incoming = [dict(r) for r in cur.execute(sql.format(col="invitee_email"), (email,)).fetchall()]
        outgoing = [dict(r) for r in cur.execute(sql.format(col="inviter_email"), (email,)).fetchall()]
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
        # 트랜잭션 안에서 pending→accepted 를 원자적으로 '선점'한다.
        # 더블탭(동시 수락 2회)이면 두 번째 UPDATE 는 rowcount=0 → 커플 중복 생성 방지.
        claimed = cur.execute(
            "UPDATE couple_invites SET status='accepted', responded_at=? "
            "WHERE id=? AND status='pending'", (_now(), iid)).rowcount
        if not claimed:
            raise ValueError("invite_not_found")
        # 해제했던 같은 두 사람이면 옛 커플을 되살려 사진·일정·설정을 그대로 돌려준다.
        old = cur.execute(
            "SELECT id FROM couples WHERE ended_at IS NOT NULL AND "
            "((member_a=? AND member_b=?) OR (member_a=? AND member_b=?)) "
            "ORDER BY id DESC LIMIT 1", (inviter, invitee, invitee, inviter)).fetchone()
        if old:
            cid = old["id"]
            cur.execute("UPDATE couples SET ended_at=NULL WHERE id=?", (cid,))
        else:
            cur.execute("INSERT INTO couples (member_a, member_b, created_at) VALUES (?, ?, ?)",
                        (inviter, invitee, _now()))
            cid = cur.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for em in (inviter, invitee):
            cur.execute("INSERT INTO users (email, couple_id) VALUES (?, ?) "
                        "ON CONFLICT(email) DO UPDATE SET couple_id=excluded.couple_id", (em, cid))
        cur.execute(
            "UPDATE couple_invites SET status='canceled', responded_at=? "
            "WHERE status='pending' AND id!=? AND "
            "(inviter_email IN (?,?) OR invitee_email IN (?,?))",
            (_now(), iid, inviter, invitee, inviter, invitee))
    return {"couple_id": cid, "partner": inviter}


def decline_invite(invitee: str, iid: str) -> None:
    invitee = _norm(invitee)
    inv = _get_invite(iid)
    if not inv or inv["status"] != "pending" or inv["invitee_email"] != invitee:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='declined', responded_at=? "
                    "WHERE id=? AND status='pending'", (_now(), iid))


def cancel_invite(inviter: str, iid: str) -> None:
    inviter = _norm(inviter)
    inv = _get_invite(iid)
    if not inv or inv["status"] != "pending" or inv["inviter_email"] != inviter:
        raise ValueError("invite_not_found")
    with cursor() as cur:
        cur.execute("UPDATE couple_invites SET status='canceled', responded_at=? "
                    "WHERE id=? AND status='pending'", (_now(), iid))


def unlink(couple_id: int) -> list[str]:
    """커플 해제: couples.ended_at 기록 + 양쪽 couple_id=NULL. 데이터는 옛 couple_id 로 남아
    둘 다 볼 수 없고, 같은 두 사람이 다시 연결하면 accept_invite 가 되살린다.
    반환: 해제된 멤버 이메일 목록(알림용)."""
    from .db import couple_members
    members = couple_members(couple_id)
    with cursor() as cur:
        cur.execute("UPDATE users SET couple_id=NULL WHERE couple_id=?", (couple_id,))
        cur.execute("UPDATE couples SET ended_at=? WHERE id=?", (_now(), couple_id))
    return members


COUPLE_TABLES = ("events", "places", "bucket", "photos", "pokes", "chat_messages",
                 "daily_notes", "settings_kv")


def purge_couple(couple_id: int) -> None:
    """커플의 모든 데이터(사진 파일 포함)와 couples 행을 지운다."""
    from .routes.photos_routes import _purge
    with cursor() as cur:
        photos = cur.execute("SELECT id, filename FROM photos WHERE couple_id=?",
                             (couple_id,)).fetchall()
        for table in COUPLE_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE couple_id=?", (couple_id,))
        cur.execute("DELETE FROM couples WHERE id=?", (couple_id,))
    for r in photos:
        _purge(r["id"], r["filename"])


def delete_account(email: str) -> list[str]:
    """회원 탈퇴: 현재 커플 해제 → 이 계정이 속했던 모든 커플 데이터·초대·계정 삭제.
    상대 계정은 남고 미매칭 상태가 된다. 반환: 알림 보낼 전 상대 목록."""
    email = _norm(email)
    cid = couple_of(email)
    partners = [m for m in (unlink(cid) if cid else []) if m != email]
    with cursor() as cur:
        cids = [r["id"] for r in cur.execute(
            "SELECT id FROM couples WHERE member_a=? OR member_b=?", (email, email)).fetchall()]
        cur.execute("DELETE FROM couple_invites WHERE inviter_email=? OR invitee_email=?",
                    (email, email))
        cur.execute("DELETE FROM login_codes WHERE email=?", (email,))
        cur.execute("DELETE FROM users WHERE email=?", (email,))
    for c in cids:
        purge_couple(c)
    return partners

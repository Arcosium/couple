"""저장 데이터 암호화(AES-256-GCM). 키는 vault .env 의 COUPLE_DATA_KEY(32바이트, base64url).

DB 파일·사진 파일만 새어 나가는 경우(백업·사본 유출)를 막는다. 서버가 키를 가지므로
서버 침해는 막지 못한다. 암호화 전 평문은 그대로 통과시켜 이관 중·옛 백업 복원과 호환된다.
"""
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

_PREFIX = "enc1:"          # DB 텍스트
_MAGIC = b"CPLE1\0"        # 파일 머리


def _load() -> AESGCM:
    raw = settings.data_key
    if not raw:
        # 키 없이 뜨면 새 데이터가 평문으로 쌓인다 → 기동을 막는다.
        raise RuntimeError("COUPLE_DATA_KEY 없음: vault couple/.env 에 32바이트 키가 필요하다")
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) != 32:
        raise RuntimeError("COUPLE_DATA_KEY 는 32바이트여야 한다")
    return AESGCM(key)


_aes = _load()


def enc_text(s):
    if not isinstance(s, str) or not s or s.startswith(_PREFIX):
        return s
    n = os.urandom(12)
    return _PREFIX + base64.urlsafe_b64encode(n + _aes.encrypt(n, s.encode(), None)).decode()


def dec_text(s):
    if not isinstance(s, str) or not s.startswith(_PREFIX):
        return s
    try:
        b = base64.urlsafe_b64decode(s[len(_PREFIX):])
        return _aes.decrypt(b[:12], b[12:], None).decode()
    except Exception:
        return s          # 우연히 'enc1:' 로 시작하는 평문


def enc_bytes(b: bytes) -> bytes:
    if b.startswith(_MAGIC):
        return b
    n = os.urandom(12)
    return _MAGIC + n + _aes.encrypt(n, b, None)


def dec_bytes(b: bytes) -> bytes:
    if not b.startswith(_MAGIC):
        return b
    h = len(_MAGIC)
    return _aes.decrypt(b[h:h + 12], b[h + 12:], None)


def read_file(p: Path) -> bytes:
    return dec_bytes(p.read_bytes())


def write_file(p: Path, data: bytes) -> None:
    """임시 파일에 쓴 뒤 교체 → 읽는 중인 요청이 반쯤 쓴 파일을 보지 않는다."""
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(enc_bytes(data))
    os.replace(tmp, p)


def encrypt_dir(d: Path) -> int:
    """폴더 안 평문 파일을 암호화(멱등). 바꾼 개수 반환."""
    n = 0
    for p in d.iterdir() if d.exists() else ():
        if not p.is_file() or p.name.endswith(".tmp"):
            continue
        with open(p, "rb") as f:
            if f.read(len(_MAGIC)) == _MAGIC:
                continue
        write_file(p, p.read_bytes())
        n += 1
    return n

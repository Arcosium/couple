import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
# 통합 .env (전 프로젝트 공용). 키 회전은 /home/opc/projects/.env 한 곳에서.
load_dotenv("/home/opc/projects/.env")


def _emails():
    raw = os.getenv("ALLOWED_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


class Settings:
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    uploads_dir: Path = BASE_DIR / "uploads" / "photos"
    db_path: Path = Path(os.getenv("COUPLE_DB") or (BASE_DIR / "data" / "couple.db"))

    session_cookie: str = os.getenv("SESSION_COOKIE", "couple_session")
    # 자체 아이디+비밀번호 인증 플래그
    allow_signup: bool = os.getenv("ALLOW_SIGNUP", "1").lower() in ("1", "true", "yes")
    allow_legacy_claim: bool = os.getenv("ALLOW_LEGACY_CLAIM", "1").lower() in ("1", "true", "yes")
    # DEPRECATED(2026-06-21): CF Access·화이트리스트 폐기. conftest 시드에서만 참조.
    open_signup: bool = os.getenv("OPEN_SIGNUP", "").lower() in ("1", "true", "yes")
    allowed_emails: list[str] = _emails()

    secret_key: str = os.getenv("SECRET_KEY", "dev-insecure-change-me")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    kakao_js_key: str = os.getenv("KAKAO_JS_KEY", "")
    kakao_rest_key: str = os.getenv("KAKAO_REST_KEY", "")

    anniversary_date: str = os.getenv("ANNIVERSARY_DATE", "2026-01-01")
    nickname_a: str = os.getenv("NICKNAME_A", "자기")
    nickname_b: str = os.getenv("NICKNAME_B", "애기")
    birthday_a: str = os.getenv("BIRTHDAY_A", "")  # nickname_a(=allowed_emails[0]) 생일
    birthday_b: str = os.getenv("BIRTHDAY_B", "")  # nickname_b(=allowed_emails[1]) 생일

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587") or 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "")

    port: int = int(os.getenv("PORT", "8800") or 8800)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()

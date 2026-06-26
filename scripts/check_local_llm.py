"""로컬 LLM(OpenAI 호환) 연동 점검. 실행:
    python3.11 scripts/check_local_llm.py        # 또는 python3.12

couple/.env 의 LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL 을 그대로 읽어
  1) /v1/models 로 모델 목록을 받고 설정 모델이 있는지 확인
  2) /v1/chat/completions 로 한 번 호출해 200 + 비어있지 않은 응답을 받는지 확인
한다. 서버가 안 떠 있으면 연결 실패 메시지와 함께 exit code 2(=skip 성격)로 끝낸다.
설정은 맞는데 호출이 실패하면 exit code 1(=실제 버그)로 끝낸다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import settings as cfg


def main() -> int:
    base = (cfg.local_llm_base_url or "").rstrip("/")
    if not base:
        print("✗ LOCAL_LLM_BASE_URL 미설정 (.env 또는 config 기본값 확인)")
        return 2
    print(f"base_url = {base}")
    print(f"model    = {cfg.local_llm_model}")

    # 1) 모델 목록
    try:
        r = httpx.get(base + "/models", timeout=5.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        print(f"⊘ 서버 연결 불가 ({base}/models): {exc!r} — 서버 미가동으로 간주 (skip)")
        return 2
    if r.status_code != 200:
        print(f"⊘ /models 가 {r.status_code} 반환 — 서버 미가동으로 간주 (skip)")
        return 2
    served = [m.get("id") for m in (r.json().get("data") or [])]
    print(f"served models = {served}")

    # Ollama 는 `name` 과 `name:latest` 를 동일 모델로 취급하므로 :latest 태그를 정규화해 비교.
    def _norm(name: str) -> str:
        return name[: -len(":latest")] if name.endswith(":latest") else name

    served_norm = {_norm(m) for m in served if m}
    if _norm(cfg.local_llm_model) not in served_norm:
        print(f"✗ 설정 모델 {cfg.local_llm_model!r} 이 목록에 없음 — 모델 id 불일치 (버그)")
        return 1
    print("✓ 설정 모델이 서버 목록에 존재(:latest 태그 무시)")

    # 2) chat/completions 실호출
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    try:
        r = httpx.post(
            url,
            json={
                "model": cfg.local_llm_model,
                "messages": [{"role": "user", "content": "안녕, 한 문장으로만 답해줘."}],
                "max_tokens": 64,
            },
            timeout=cfg.local_llm_timeout_seconds,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        print(f"⊘ chat/completions 연결 불가: {exc!r} (skip)")
        return 2
    if r.status_code != 200:
        print(f"✗ chat/completions {r.status_code}: {r.text[:300]} (버그)")
        return 1
    data = r.json()
    msg = (data.get("choices") or [{}])[0].get("message", {})
    text = (msg.get("content") or "") or (msg.get("reasoning") or "")
    if not text.strip():
        print(f"✗ 응답이 비어있음: {msg!r} (버그)")
        return 1
    print(f"✓ chat/completions 200 — 응답 일부: {text.strip()[:120]!r}")
    print("== 로컬 LLM 연동 정상 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

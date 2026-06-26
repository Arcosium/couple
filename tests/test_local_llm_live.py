"""로컬 LLM 라이브 연동 검증.

설정된 LOCAL_LLM_BASE_URL / LOCAL_LLM_MODEL 로 실제 OpenAI 호환 서버에
한 번 chat/completions 를 호출해, 200 + 비어있지 않은 응답을 받는지 확인한다.

서버가 떠 있지 않으면(네트워크/연결 실패) pytest.skip 으로 건너뛴다 —
CI/오프라인 환경에서 빨간불이 뜨지 않게. 모델 식별자가 틀리면(서버는 살아있는데
4xx) 그건 진짜 설정 버그이므로 fail 시킨다.
"""
import httpx
import pytest

from app.config import settings as cfg
from app.routes import chat_routes


def _require_live_server() -> None:
    """LLM 서버가 떠 있고 모델 목록을 주는지 확인. 안 떠 있으면 skip."""
    if not cfg.local_llm_base_url:
        pytest.skip("LOCAL_LLM_BASE_URL 미설정 — 로컬 LLM 라이브 테스트 건너뜀")
    models_url = cfg.local_llm_base_url.rstrip("/") + "/models"
    try:
        resp = httpx.get(models_url, timeout=3.0)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        pytest.skip(f"로컬 LLM 서버에 연결 불가 ({models_url}): {exc!r}")
    if resp.status_code != 200:
        pytest.skip(f"{models_url} 가 {resp.status_code} 반환 — 서버 미가동으로 간주, 건너뜀")
    return resp.json()


def _norm_tag(name: str) -> str:
    """Ollama 는 `name` 과 `name:latest` 를 동일 모델로 취급 → :latest 정규화."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def test_configured_model_is_served():
    """설정한 LOCAL_LLM_MODEL 이 /v1/models 목록에 실제로 존재하는지 확인(:latest 무시)."""
    payload = _require_live_server()
    served = {_norm_tag(m.get("id") or "") for m in (payload.get("data") or [])}
    assert _norm_tag(cfg.local_llm_model) in served, (
        f"설정 모델 {cfg.local_llm_model!r} 이 서버 모델 목록에 없음: {sorted(served)}"
    )


def test_local_completion_returns_response():
    """앱이 실제 쓰는 _local_completion 경로로 한 turn 호출 → 200 + 응답 구조 확인."""
    _require_live_server()
    messages = [{"role": "user", "content": "안녕, 한 문장으로만 답해줘."}]
    import asyncio

    result = asyncio.run(chat_routes._local_completion(messages, tools=[]))

    assert result["choices"], f"choices 가 비어있음: {result!r}"
    msg = result["choices"][0]["message"]
    # Ollama 는 잘릴 때 content 가 비고 reasoning 에 담길 수 있어 둘 다 허용.
    text = (msg.get("content") or "") or (msg.get("reasoning") or "")
    assert text.strip(), f"비어있지 않은 응답을 기대했으나 비어있음: {msg!r}"

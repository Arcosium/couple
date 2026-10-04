"""사진 자동 태깅 — 로컬 멀티모달 LLM(:11434)이 사진을 보고 해시태그를 붙인다.

업로드 응답을 막지 않도록 백그라운드 태스크로 돌고, 실패하면 조용히 넘어간다
(사진 자체는 이미 저장된 뒤라 태깅 실패로 업로드가 깨지면 안 된다).
"""

import asyncio
import base64
import re
from pathlib import Path

import httpx

from .config import settings
from .db import couple_members, cursor
from .realtime import hub

# 로컬 LLM 은 한 대뿐(채팅과 공유) → 동시 요청을 묶어 코코 응답이 밀리지 않게 한다.
_sem = asyncio.Semaphore(2)
_tasks: set[asyncio.Task] = set()   # create_task 결과를 안 잡으면 GC 가 태스크를 죽인다

MAX_TAGS = 6
PROMPT = (
    "이 사진에 검색용 태그를 3~6개 붙여줘. "
    "보이는 것(사물·장소·인물·활동·분위기) 위주의 한국어 명사이고 각 1~3단어. "
    "쉼표로 구분해 태그만 한 줄로 출력하고 다른 말은 절대 하지 마. "
    "예시: 카페, 디저트, 데이트"
)


def parse_tags(text: str) -> list[str]:
    """모델 답변에서 태그 목록만 뽑는다(사설·<think>·번호매김·해시 기호 제거)."""
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I)
    # 모델이 설명을 덧붙이면 쉼표가 있는 마지막 줄이 실제 태그 줄이다.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    line = next((ln for ln in reversed(lines) if "," in ln), lines[-1] if lines else "")
    out: list[str] = []
    for raw in re.split(r"[,\n]", line):
        t = re.sub(r"^[\s\-*•#0-9.)]+", "", raw).strip().strip("\"'.#")
        if t and len(t) <= 20 and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


async def tag_photo(pid: str, path: Path, couple_id: int | None = None) -> list[str]:
    """사진 한 장을 태깅해 저장하고 상대방 화면에도 알린다. 실패하면 빈 목록."""
    async with _sem:
        try:
            b64 = base64.b64encode(path.read_bytes()).decode()
            async with httpx.AsyncClient(timeout=settings.local_llm_timeout_seconds) as client:
                r = await client.post(
                    settings.local_llm_base_url.rstrip("/") + "/chat/completions",
                    json={
                        "model": settings.local_llm_model,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ]}],
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                )
                r.raise_for_status()
                tags = parse_tags(r.json()["choices"][0]["message"].get("content") or "")
        except Exception:
            return []
    if not tags:
        return []
    with cursor() as cur:
        # 사람이 이미 손댄 태그는 덮어쓰지 않는다.
        cur.execute("UPDATE photos SET tags=? WHERE id=? AND (tags IS NULL OR tags='')",
                    (",".join(tags), pid))
        changed = cur.rowcount
    if changed and couple_id:
        for em in couple_members(couple_id):
            await hub.send(em, {"kind": "photo_tagged", "id": pid, "tags": ",".join(tags)})
    return tags


def enqueue(pid: str, path: Path, couple_id: int) -> None:
    if not settings.local_llm_base_url:
        return
    task = asyncio.create_task(tag_photo(pid, path, couple_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)

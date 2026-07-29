"""
코코 챗봇 — 로컬 OpenAI 호환 LLM의 Function Calling으로 앱 데이터를 직접 조작.

흐름:
1. 사용자 메시지를 chat_messages 에 저장
2. 로컬 LLM 호출 (도구 21개 노출)
3. 응답에 function_call 이 있으면 → execute_tool 로 실행 → response 부분으로 다시 호출
4. 최대 8회 반복 후 최종 텍스트를 chat_messages 에 저장
5. 사용된 도구 목록을 함께 반환 (UI에서 ✨ 표시)
"""
import json
import secrets
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MASCOT_EMOJI = {"bunny": "🐰", "cat": "🐱", "bear": "🐻"}

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..agent_tools import TOOL_DECLARATIONS, execute_tool
from ..auth import require_couple
from ..config import settings as cfg
from ..db import cursor, kv_get

router = APIRouter(prefix="/api/chat", tags=["chat"])


SYSTEM_PROMPT = """\
너는 커플 앱 '우리만의 공간' 의 마스코트 캐릭터 '코코🐰' 야.
두 사람이 같이 쓰는 앱이고, 너는 두 가지 일을 한다:

(1) **앱 데이터 조작**: 너는 21개의 도구를 가지고 있어. 사용자가
   "홍대 정스버거 위시리스트에 추가해줘",
   "내일 오후 7시 한강 데이트 일정 잡아줘",
   "제주도 여행 버킷에 넣어줘",
   "최근 사진들 다 홍대로 태깅해줘",
   "여친한테 사랑한다고 콕 보내줘",
   "기념일 2025-02-21로 바꿔줘"
   같이 말하면 직접 도구를 호출해서 처리해.

(2) **데이트 코스 추천**: 그 외엔 다정한 추천 챗봇. 1) 오후 2시 ○○ →
   2) 오후 4시 △△ ... 처럼 시간/장소 순서로. 한국 서울/수도권 기준.

   **코스를 짤 땐(또는 "데이트 추천", "주말에 뭐하지", "코스 짜줘" 류 요청이면)
   추측하기 전에 항상 아래를 먼저 조회해서 우리 데이터에 맞춰 짜:**
   1) **list_places** — 저장된 장소. '또갈곳(revisit)' → '가볼곳(wishlist)' 순으로 우선
      활용. 동선이 자연스럽게 이어지게 묶어.
   2) **list_bucket** — 아직 못 이룬(done=false) 버킷리스트. 코스로 풀 수 있는 항목
      (예: '루프탑 바', '미술관 데이트', '벚꽃 구경')이 있으면 자연스럽게 코스에 녹이고
      "이거 버킷에 있던 거야 ✨" 라고 한 줄로 짚어줘.
   3) **list_events** — 캘린더 일정. (a) 코스 날짜에 이미 잡힌 일정이 있으면 겹치지 않게
      그 시간 전후로 짜고, (b) 기념일·생일이 코스 날짜 근처면 그 분위기를 살짝 반영해.
   4) **list_festivals** — 그 무렵/그 지역에 열리는 축제. 있으면 "마침 그때 ○○축제 해! 🎆"
      라고 코스에 녹여. "이번 주말/근처 축제 있어?" 류 질문엔 이걸 바로 써(days=7).
   5) 저장된 곳이 마땅찮으면 새 장소를 제안하되 —
      · **관광명소·여행지**(다른 지역 나들이/여행: '제주 가볼 곳','강릉 여행','실내 문화시설')는
        **tour_search** (사진·좌표 있는 공식 관광 데이터, 오프라인이라 항상 됨).
      · **동네 특정 맛집·카페**(정확한 상호명)는 **kakao_search**.

   코스를 다 제안한 뒤엔 **"이 코스 캘린더에 넣어둘까?"** 라고 물어보고, 좋다고 하면
   add_event 로 일정을 잡고 핵심 장소는 add_place(kind='wishlist')로 저장해줘.
   (묻지도 않고 미리 저장하진 마 — 먼저 제안하고 동의받은 뒤에.)

도구 사용 규칙:
- 실제 장소(맛집·카페 등)는 반드시 **kakao_search 먼저** → 그 결과의 lat/lng/address 로
  add_place 호출. 좌표 없으면 add_place 부르지 마.
- **kakao_search 가 error 를 반환하면 같은 도구를 재시도하지 마.** 사용자에게
  "🥲 카카오 검색이 안 돼 — developers.kakao.com 에서 OPEN_MAP_AND_LOCAL 활성화해줘.
   대신 좌표 알려주면 바로 추가할게" 같이 한 번에 안내하고 끝내.
- **kakao_search 결과가 0개면** "그 이름으론 못 찾았어, 좀 더 자세히 알려줄래?" 라고 묻고 끝내.
- **tour_search / list_festivals 는 오프라인 관광 데이터**라 항상 동작한다(키 필요 없음).
  결과에 이미 lat/lng 가 있으니 add_place 에 그대로 넘겨. 결과의 image 는 사진 URL이니
  마음에 들 만한 곳이면 한 곳 정도 자연스럽게 소개해도 좋아.
- 수정/삭제는 **list_* 로 ID 먼저 확인** 후 update_*/delete_* 호출.
- 여러 개를 한꺼번에 지울 땐 "정말 ○개 다 지울까?" 라고 한번 확인.
- 모호하면 추측 말고 물어봐("어느 정스버거? 홍대점? 강남점?").
- 도구 결과를 사용자에게 한국어 반말로 짧고 다정하게 알려줘.
  "📌 정스버거(홍대점) 위시리스트에 담았어!" 같이.

스타일:
- 한국어 반말, 이모지는 줄당 1~2개 가볍게.
- 답은 짧고 구체적, 장황한 설명 금지.
- 실시간 정보(영업시간, 날씨)는 모르면 "직접 확인해봐" 라고 솔직히.

오늘 날짜는 시스템이 자동으로 알려줄게 — 사용자가 "내일", "다음 주말" 같이 말하면
그 기준으로 YYYY-MM-DD 변환해서 도구에 넘겨.
"""


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


def _greeting(couple_id: int, user_email: str) -> str:
    a = kv_get(couple_id, "nickname_a", cfg.nickname_a)
    b = kv_get(couple_id, "nickname_b", cfg.nickname_b)
    from ..db import couple_members
    members = couple_members(couple_id)
    target = a if members and user_email == members[0] else b
    hour = datetime.now(KST).hour          # KST 기준으로 아침/점심/오후/저녁/밤 판정
    if 5 <= hour < 11: t = "아침"
    elif 11 <= hour < 14: t = "점심"
    elif 14 <= hour < 18: t = "오후"
    elif 18 <= hour < 22: t = "저녁"
    else: t = "밤"
    emoji = MASCOT_EMOJI.get(kv_get(couple_id, "mascot", "bunny"), "🐰")   # 현재 마스코트에 맞춰
    return (f"{target}야~ {t}이네 {emoji} 오늘 뭐 하고 싶어?\n"
            f"(맛집·일정·버킷 같은 거 말로만 시켜도 내가 앱에 바로 넣어줄게)")


def _load_history(couple_id: int, session_id: str, limit: int = 12) -> list[dict]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT role, content, user_email, created_at FROM chat_messages "
            "WHERE couple_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
            (couple_id, session_id, limit),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def _save_msg(couple_id: int, session_id: str, role: str, content: str, email: str | None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO chat_messages (id, role, content, session_id, user_email, created_at, couple_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (secrets.token_urlsafe(8), role, content, session_id, email,
             datetime.now().isoformat(timespec="seconds"), couple_id),
        )


def _chat_completions_url(base_url: str) -> str:
    """LOCAL_LLM_BASE_URL은 /v1 루트 또는 완전한 chat/completions URL을 허용한다."""
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"


async def _local_completion(messages: list[dict], tools: list[dict]) -> dict:
    """API 키 없이 로컬 OpenAI 호환 서버에 한 번 요청한다."""
    payload = {
        "model": cfg.local_llm_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=cfg.local_llm_timeout_seconds) as client:
        response = await client.post(_chat_completions_url(cfg.local_llm_base_url), json=payload)
    response.raise_for_status()
    return response.json()


async def _ai_turn(user_email: str, couple_id: int, history: list[dict], message: str) -> tuple[str, list[str]]:
    """로컬 OpenAI 호환 모델로 한 turn 처리. 반환: (텍스트, 사용 도구)."""
    if not cfg.local_llm_base_url:
        return (
            "(로컬 모델 서버 주소가 아직 설정되지 않았어. "
            "`.env`에 LOCAL_LLM_BASE_URL을 넣고 서비스 재시작해줘) "
            "오늘은 둘이 가까운 카페에서 디저트 어때? ☕💖",
            [],
        )

    today = datetime.now(KST).strftime("%Y-%m-%d (%a) %H:%M")
    tools = [{"type": "function", "function": declaration}
             for declaration in TOOL_DECLARATIONS[0]["function_declarations"]]
    messages = [{"role": "system", "content": SYSTEM_PROMPT + f"\n\n[현재 시각] {today}"}]
    messages.extend({"role": "user" if m["role"] == "user" else "assistant", "content": m["content"]}
                    for m in history)
    messages.append({"role": "user", "content": message})

    try:
        response = await _local_completion(messages, tools)
    except Exception as e:
        return (f"🥹 (코코가 잠깐 멍해졌어 — {e.__class__.__name__}: {e})", [])

    tools_used: list[str] = []
    final = ""
    for it in range(5):
        try:
            assistant_message = response["choices"][0]["message"]
            tool_calls = assistant_message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError) as e:
            print(f"[chat iter {it}] invalid response: {e}", flush=True)
            return ("🥹 (로컬 모델 응답 형식이 올바르지 않아. 서버 로그를 확인해줘)", tools_used)

        if not tool_calls:
            final = assistant_message.get("content") or ""
            break

        messages.append({"role": "assistant", "content": assistant_message.get("content") or "",
                         "tool_calls": tool_calls})
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            args: dict = {}
            try:
                raw_args = function.get("arguments") or "{}"
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                result = {"error": f"invalid tool arguments: {e}"}
            else:
                result = await execute_tool(name, args, user_email, couple_id)
            tools_used.append(name)
            print(f"[chat tool] {name}({args}) → {str(result)[:200]}", flush=True)
            messages.append({"role": "tool", "tool_call_id": call.get("id", name), "name": name,
                             "content": json.dumps(result, ensure_ascii=False, default=str)})

        try:
            response = await _local_completion(messages, tools)
        except Exception as e:
            return (f"🥹 (도구 결과 전달 실패: {e.__class__.__name__}: {e})", tools_used)

    if not final:
        final = "처리됐어 🐰" if tools_used else "(코코가 할 말을 못 찾았어 🥺 다시 말해줄래?)"
    return (final, tools_used)


@router.get("/greeting")
def greeting(request: Request):
    email, cid = require_couple(request)
    return {"text": _greeting(cid, email)}


@router.get("/history")
def history(request: Request, session_id: str = "default"):
    _email, cid = require_couple(request)
    return _load_history(cid, session_id, limit=80)


@router.post("/send")
async def send(body: ChatIn, request: Request):
    email, cid = require_couple(request)
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="empty")
    sid = (body.session_id or "default")[:64]
    _save_msg(cid, sid, "user", msg, email)
    # 컨텍스트로 보낼 직전까지의 기록 (방금 user 메시지 제외)
    hist = _load_history(cid, sid, limit=12)[:-1]
    reply, tools = await _ai_turn(email, cid, hist, msg)
    _save_msg(cid, sid, "assistant", reply, None)
    return {"reply": reply, "tools_used": tools}


@router.delete("/history")
def clear(request: Request, session_id: str = "default"):
    _email, cid = require_couple(request)
    with cursor() as cur:
        cur.execute("DELETE FROM chat_messages WHERE couple_id=? AND session_id=?", (cid, session_id))
    return {"ok": True}

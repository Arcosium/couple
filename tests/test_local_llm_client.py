import asyncio

from app.routes import chat_routes


def test_local_completion_posts_openai_format_without_auth(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "안녕"}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(chat_routes.cfg, "local_llm_base_url", "http://127.0.0.1:8080/v1")
    monkeypatch.setattr(chat_routes.cfg, "local_llm_model", "local-qwen")
    monkeypatch.setattr(chat_routes.httpx, "AsyncClient", lambda **kwargs: Client())

    result = asyncio.run(chat_routes._local_completion([{"role": "user", "content": "hi"}], []))

    assert result["choices"][0]["message"]["content"] == "안녕"
    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert captured["json"]["model"] == "local-qwen"
    assert "headers" not in captured


def test_ai_turn_executes_and_returns_local_tool_call(monkeypatch):
    responses = iter([
        {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "list_bucket", "arguments": "{}"},
        }]}}]},
        {"choices": [{"message": {"content": "버킷리스트를 확인했어 🐰"}}]},
    ])
    requests = []

    async def completion(messages, tools):
        requests.append(messages)
        return next(responses)

    async def execute(name, args, email, couple_id):
        assert (name, args, email, couple_id) == ("list_bucket", {}, "a@test", 1)
        return {"items": []}

    monkeypatch.setattr(chat_routes.cfg, "local_llm_base_url", "http://local/v1")
    monkeypatch.setattr(chat_routes, "_local_completion", completion)
    monkeypatch.setattr(chat_routes, "execute_tool", execute)

    reply, tools_used = asyncio.run(chat_routes._ai_turn("a@test", 1, [], "버킷 보여줘"))

    assert reply == "버킷리스트를 확인했어 🐰"
    assert tools_used == ["list_bucket"]
    assert requests[1][-1] == {
        "role": "tool", "tool_call_id": "call_1", "name": "list_bucket", "content": '{"items": []}'
    }

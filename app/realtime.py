import asyncio
import json
from collections import defaultdict
from fastapi import WebSocket


class Hub:
    """이메일별 활성 WebSocket 연결. 같은 사용자가 여러 탭/기기를 열 수 있다."""

    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, email: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns[email].add(ws)

    async def disconnect(self, email: str, ws: WebSocket) -> None:
        async with self._lock:
            self._conns[email].discard(ws)
            if not self._conns[email]:
                self._conns.pop(email, None)

    async def send(self, email: str, payload: dict) -> int:
        data = json.dumps(payload, ensure_ascii=False)
        sent = 0
        dead: list[WebSocket] = []
        async with self._lock:
            targets = list(self._conns.get(email, ()))
        for ws in targets:
            try:
                await ws.send_text(data)
                sent += 1
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._conns[email].discard(ws)
        return sent

    async def drop(self, email: str) -> None:
        """세션을 끊은 계정(로그아웃·비번 변경·탈퇴)의 열린 연결을 닫는다."""
        async with self._lock:
            conns = self._conns.pop(email, set())
        for ws in conns:
            try:
                await ws.close(code=4401)
            except Exception:
                pass


hub = Hub()

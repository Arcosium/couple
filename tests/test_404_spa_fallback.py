"""404 → SPA 폴백. index() 는 동기 함수라 await 하면 TypeError 로 500 이 됐다."""
from fastapi.testclient import TestClient

import server


def test_unknown_path_renders_index_not_500():
    c = TestClient(server.app, raise_server_exceptions=False)
    assert c.get("/zzz-no-such-path").status_code == 200


def test_unknown_api_path_stays_404():
    c = TestClient(server.app, raise_server_exceptions=False)
    assert c.get("/api/zzz").status_code == 404

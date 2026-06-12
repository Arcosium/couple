from app import kakao_import

SAMPLE = '''<html><body>
<script id="data" type="application/json">
{"places":[{"name":"연남동 카페","x":"126.92","y":"37.56","road_address":"서울 마포구 ..."},
           {"name":"망원 칼국수","x":"126.90","y":"37.55","road_address":""}]}
</script></body></html>'''


def test_parse_folder_extracts_places():
    items = kakao_import.parse_kakao_folder(SAMPLE)
    assert len(items) == 2
    assert items[0]["name"] == "연남동 카페"
    assert abs(items[0]["lat"] - 37.56) < 0.01
    assert abs(items[0]["lng"] - 126.92) < 0.01


def test_parse_empty_returns_empty():
    assert kakao_import.parse_kakao_folder("<html></html>") == []

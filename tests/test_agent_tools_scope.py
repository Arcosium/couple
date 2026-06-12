import asyncio
from app import agent_tools, db


def test_add_and_list_place_scoped_by_couple():
    async def go():
        await agent_tools.add_place("z1@t", 77, name="Z장소", kind="wishlist",
                                    lat=37.5, lng=127.0)
        mine = await agent_tools.list_places(77)
        other = await agent_tools.list_places(88)
        return mine, other
    mine, other = asyncio.run(go())
    assert any(p["name"] == "Z장소" for p in mine["items"])
    assert all(p["name"] != "Z장소" for p in other["items"])

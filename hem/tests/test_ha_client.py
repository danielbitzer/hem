"""HaClient/Publisher tests against a real in-process aiohttp server."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from hem.config import HaConnection
from hem.ha.client import EntityNotFoundError, HaClient
from hem.ha.publisher import Publisher

SOC_STATE = {
    "entity_id": "sensor.battery_level",
    "state": "72.5",
    "attributes": {"unit_of_measurement": "%"},
    "last_updated": "2026-07-15T09:00:00.000000+00:00",
}


class FakeHa:
    """Minimal /api/states surface with request capture."""

    def __init__(self) -> None:
        self.states: dict[str, dict] = {}
        self.posted: list[tuple[str, dict]] = []
        self.app = web.Application()
        self.app.router.add_get("/api/", self._root)
        self.app.router.add_get("/api/states/{entity_id}", self._get_state)
        self.app.router.add_post("/api/states/{entity_id}", self._post_state)

    async def _root(self, request: web.Request) -> web.Response:
        return web.json_response({"message": "API running."})

    async def _get_state(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        if entity_id not in self.states:
            return web.json_response({"message": "not found"}, status=404)
        return web.json_response(self.states[entity_id])

    async def _post_state(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        self.posted.append((entity_id, await request.json()))
        return web.json_response({}, status=201)


@asynccontextmanager
async def fake_ha_client(fake: FakeHa) -> AsyncIterator[HaClient]:
    server = TestServer(fake.app)
    await server.start_server()
    try:
        base = str(server.make_url("")).rstrip("/")
        conn = HaConnection(rest_url=f"{base}/api", ws_url="ws://unused", token="tok")
        async with HaClient(conn) as client:
            yield client
    finally:
        await server.close()


async def test_get_state():
    fake = FakeHa()
    fake.states["sensor.battery_level"] = SOC_STATE
    async with fake_ha_client(fake) as client:
        assert await client.api_ok()
        state = await client.get_state("sensor.battery_level")
    assert state.as_float() == 72.5
    assert state.available
    assert state.last_updated.tzinfo is not None


async def test_unavailable_state():
    fake = FakeHa()
    fake.states["sensor.battery_level"] = dict(SOC_STATE, state="unavailable")
    async with fake_ha_client(fake) as client:
        state = await client.get_state("sensor.battery_level")
    assert not state.available


async def test_missing_entity_raises():
    fake = FakeHa()
    async with fake_ha_client(fake) as client:
        with pytest.raises(EntityNotFoundError, match="sensor.nope"):
            await client.get_state("sensor.nope")


async def test_publish_status_posts_state():
    fake = FakeHa()
    async with fake_ha_client(fake) as client:
        await Publisher(client).publish_status("ok", detail="test")
    entity_id, body = fake.posted[0]
    assert entity_id == "sensor.hem_status"
    assert body["state"] == "ok"
    assert body["attributes"]["detail"] == "test"
    assert "heartbeat" in body["attributes"]

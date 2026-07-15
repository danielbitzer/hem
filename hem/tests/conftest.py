from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from hem.config import HaConnection
from hem.ha.client import HaClient, State

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture_state(name: str) -> State:
    """Load a captured entity state (tests/fixtures/<name>.yaml) as a State."""
    data = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text())
    return State(
        entity_id=data["entity_id"],
        state=str(data["state"]),
        attributes=data["attributes"],
        last_updated=datetime.fromisoformat(data["last_updated"]),
    )


def fixture_state_payload(name: str) -> dict:
    """Same fixture as a raw /api/states response dict (for FakeHa)."""
    data = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text())
    return {
        "entity_id": data["entity_id"],
        "state": str(data["state"]),
        "attributes": data["attributes"],
        "last_updated": data["last_updated"],
    }


class FakeHa:
    """Minimal /api/states surface with request capture."""

    def __init__(self) -> None:
        self.states: dict[str, dict] = {}
        self.posted: list[tuple[str, dict]] = []
        self.service_calls: list[tuple[str, str, dict]] = []
        self.service_responses: dict[tuple[str, str], dict] = {}
        # fault injection: entity_id -> HTTP status for GET /states/<id>
        self.state_errors: dict[str, int] = {}
        # fault injection: called with (domain, service, data), return an HTTP
        # status to fail that call, or None to succeed
        self.service_fault: object = None
        self.app = web.Application()
        self.app.router.add_get("/api/", self._root)
        self.app.router.add_get("/api/states/{entity_id}", self._get_state)
        self.app.router.add_post("/api/states/{entity_id}", self._post_state)
        self.app.router.add_post("/api/services/{domain}/{service}", self._call_service)

    def add_fixture(self, name: str) -> str:
        payload = fixture_state_payload(name)
        self.states[payload["entity_id"]] = payload
        return payload["entity_id"]

    async def _root(self, request: web.Request) -> web.Response:
        return web.json_response({"message": "API running."})

    async def _get_state(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        if entity_id in self.state_errors:
            return web.json_response({"message": "injected"}, status=self.state_errors[entity_id])
        if entity_id not in self.states:
            return web.json_response({"message": "not found"}, status=404)
        return web.json_response(self.states[entity_id])

    async def _post_state(self, request: web.Request) -> web.Response:
        entity_id = request.match_info["entity_id"]
        self.posted.append((entity_id, await request.json()))
        return web.json_response({}, status=201)

    async def _call_service(self, request: web.Request) -> web.Response:
        domain, service = request.match_info["domain"], request.match_info["service"]
        data = await request.json()
        self.service_calls.append((domain, service, data))
        if self.service_fault:
            status = self.service_fault(domain, service, data)  # type: ignore[operator]
            if status:
                return web.json_response({"message": "injected"}, status=status)
        if "return_response" in request.query_string:
            response = self.service_responses.get((domain, service), {})
            return web.json_response({"changed_states": [], "service_response": response})
        return web.json_response([])


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

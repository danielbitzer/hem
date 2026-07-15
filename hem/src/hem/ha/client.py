"""Async Home Assistant client (REST now; WebSocket subscription arrives in Phase 2).

Works identically against the Supervisor proxy (add-on) and a direct HA URL with a
long-lived token (dev) — the difference is entirely in the HaConnection it's given.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Self

import aiohttp

from hem.config import HaConnection

log = logging.getLogger(__name__)

UNAVAILABLE_STATES = frozenset({"unavailable", "unknown", ""})


class EntityNotFoundError(Exception):
    def __init__(self, entity_id: str):
        super().__init__(f"Entity not found in Home Assistant: {entity_id}")
        self.entity_id = entity_id


@dataclass(frozen=True)
class State:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_updated: datetime

    @property
    def available(self) -> bool:
        return self.state not in UNAVAILABLE_STATES

    def as_float(self) -> float:
        return float(self.state)


class HaClient:
    def __init__(self, conn: HaConnection, request_timeout_s: float = 15.0):
        self._conn = conn
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> Self:
        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._conn.token}"},
            timeout=self._timeout,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("HaClient must be used as an async context manager")
        return self._session

    def _url(self, path: str) -> str:
        return f"{self._conn.rest_url}/{path.lstrip('/')}"

    async def api_ok(self) -> bool:
        try:
            async with self.session.get(self._url("/")) as resp:
                return resp.status == 200
        except aiohttp.ClientError:
            return False

    async def get_state(self, entity_id: str) -> State:
        async with self.session.get(self._url(f"/states/{entity_id}")) as resp:
            if resp.status == 404:
                raise EntityNotFoundError(entity_id)
            resp.raise_for_status()
            data = await resp.json()
        return _parse_state(data)

    async def get_states(self, entity_ids: list[str]) -> dict[str, State]:
        """Fetch all states in one call and pick out the requested entities."""
        async with self.session.get(self._url("/states")) as resp:
            resp.raise_for_status()
            data = await resp.json()
        wanted = set(entity_ids)
        found = {s["entity_id"]: _parse_state(s) for s in data if s["entity_id"] in wanted}
        if missing := wanted - found.keys():
            raise EntityNotFoundError(", ".join(sorted(missing)))
        return found

    async def set_state(
        self, entity_id: str, state: str | float, attributes: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {"state": str(state)}
        if attributes:
            payload["attributes"] = attributes
        async with self.session.post(self._url(f"/states/{entity_id}"), json=payload) as resp:
            resp.raise_for_status()

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any], return_response: bool = False
    ) -> Any:
        url = self._url(f"/services/{domain}/{service}")
        if return_response:
            url += "?return_response"
        async with self.session.post(url, json=data) as resp:
            resp.raise_for_status()
            body = await resp.json()
        return body["service_response"] if return_response else body


def _parse_state(data: dict[str, Any]) -> State:
    return State(
        entity_id=data["entity_id"],
        state=data["state"],
        attributes=data.get("attributes", {}),
        last_updated=datetime.fromisoformat(data["last_updated"]),
    )

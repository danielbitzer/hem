"""Publish HEM output sensors via POST /api/states.

REST-created entities are ephemeral (gone on HA restart, no unique_id), so every
sensor is republished unconditionally each cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hem import __version__
from hem.ha.client import HaClient
from hem.models import Plan


class Publisher:
    def __init__(self, client: HaClient):
        self._client = client

    async def publish_status(
        self,
        status: str,
        *,
        last_solve: datetime | None = None,
        solve_ms: float | None = None,
        detail: str = "",
    ) -> None:
        attrs: dict[str, Any] = {
            "friendly_name": "HEM status",
            "icon": "mdi:heart-pulse",
            "version": __version__,
            "heartbeat": datetime.now(UTC).isoformat(),
        }
        if last_solve is not None:
            attrs["last_solve"] = last_solve.isoformat()
        if solve_ms is not None:
            attrs["solve_ms"] = round(solve_ms, 1)
        if detail:
            attrs["detail"] = detail
        await self._client.set_state("sensor.hem_status", status, attrs)

    async def publish_plan(self, plan: Plan) -> None:
        """Publish the full dry-run sensor set. Implemented in Phase 2."""
        raise NotImplementedError

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

    async def publish_plan(self, plan: Plan, capacity_kwh: float) -> None:
        """Publish the full dry-run sensor set (republished every cycle)."""
        step0 = plan.intervals[0]
        await self._client.set_state(
            "sensor.hem_action",
            step0.action.value,
            {
                "friendly_name": "HEM recommended action",
                "icon": "mdi:battery-charging",
                "solver_status": plan.solver_status,
                "valid_until": step0.end.isoformat(),
            },
        )
        await self._client.set_state(
            "sensor.hem_power_setpoint",
            round(step0.power_kw, 3),
            {
                "friendly_name": "HEM battery power setpoint",
                "unit_of_measurement": "kW",
                "device_class": "power",
                "icon": "mdi:battery-arrow-up-down",
                "convention": "positive = charging",
            },
        )
        await self._client.set_state(
            "sensor.hem_soc_target",
            round(100 * step0.soc_end / capacity_kwh, 1),
            {
                "friendly_name": "HEM SoC target (end of interval)",
                "unit_of_measurement": "%",
                "icon": "mdi:battery-70",
            },
        )
        await self._client.set_state(
            "sensor.hem_horizon_cost",
            round(plan.objective_cost, 2),
            {
                "friendly_name": "HEM expected horizon cost",
                "unit_of_measurement": "$",
                "icon": "mdi:cash-multiple",
                "horizon_end": plan.intervals[-1].end.isoformat(),
            },
        )
        await self._client.set_state(
            "sensor.hem_plan",
            plan.computed_at.isoformat(),
            {
                "friendly_name": "HEM plan",
                "icon": "mdi:chart-timeline-variant",
                "solve_ms": round(plan.solve_ms, 1),
                "plan": [
                    {
                        "t": iv.start.isoformat(),
                        "action": iv.action.value,
                        "power": round(iv.power_kw, 3),
                        "soc": round(100 * iv.soc_end / capacity_kwh, 1),
                        "buy": round(iv.buy, 4),
                        "sell": round(iv.sell, 4),
                        "pv": round(iv.pv_kw, 3),
                        "load": round(iv.load_kw, 3),
                        "import": round(iv.grid_import_kw, 3),
                        "export": round(iv.grid_export_kw, 3),
                        "cost": round(iv.interval_cost, 4),
                    }
                    for iv in plan.intervals
                ],
            },
        )

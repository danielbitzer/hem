"""Plan execution: dry-run (default) or Sungrow register writes via HA.

SungrowExecutor guardrails (all of these hold regardless of what the plan says):
- never writes when the user's override input_boolean is on
- write-on-change only, rate-limited to control.max_writes_per_hour
- power setpoints clamped to the configured battery limits
- shutdown() re-asserts self-consumption mode (clean-exit path; the HA-side
  watchdog automation blueprint covers unclean death — see blueprints/)
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hem.config import Settings
from hem.ha.client import HaClient
from hem.models import Action, Plan

log = logging.getLogger(__name__)


class Executor(Protocol):
    async def apply(self, plan: Plan) -> None: ...
    async def shutdown(self) -> None: ...


class DryRunExecutor:
    """Publishes nothing to the inverter; the sensors are the whole output."""

    async def apply(self, plan: Plan) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class WriteRateLimiter:
    def __init__(self, max_per_hour: int):
        self._max = max_per_hour
        self._writes: deque[datetime] = deque()

    def allow(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=1)
        while self._writes and self._writes[0] < cutoff:
            self._writes.popleft()
        if len(self._writes) >= self._max:
            return False
        self._writes.append(now)
        return True


class SungrowExecutor:
    def __init__(self, client: HaClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._ctl = settings.control.entities
        self._limiter = WriteRateLimiter(settings.control.max_writes_per_hour)
        self._last_applied: tuple[Action, float] | None = None

    async def _override_active(self) -> bool:
        try:
            state = await self._client.get_state(self._ctl.override_boolean)
        except Exception:  # noqa: BLE001 - missing helper -> no override configured
            return False
        return state.state == "on"

    async def apply(self, plan: Plan) -> None:
        step0 = plan.intervals[0]
        action = step0.action
        power_kw = self._clamp_power(action, step0.power_kw)
        desired = (action, round(power_kw, 2))

        if desired == self._last_applied:
            return
        if await self._override_active():
            log.warning("override boolean is on: skipping inverter write")
            return
        if not self._limiter.allow():
            log.warning(
                "write rate limit reached (%d/h): holding previous inverter state",
                self._settings.control.max_writes_per_hour,
            )
            return

        log.info("inverter write: %s %.2f kW", action.value, power_kw)
        if action in (Action.IDLE, Action.CURTAIL):
            await self._set_self_consumption()
        else:
            await self._select(self._ctl.ems_mode_select, self._ctl.ems_forced_option)
            cmd = (
                self._ctl.forced_charge_option
                if action == Action.CHARGE
                else self._ctl.forced_discharge_option
            )
            await self._select(self._ctl.forced_cmd_select, cmd)
            await self._client.call_service(
                "number",
                "set_value",
                {
                    "entity_id": self._ctl.forced_power_number,
                    "value": round(abs(power_kw) * 1000),  # mkaiser power number is W
                },
            )
        self._last_applied = desired

    def _clamp_power(self, action: Action, power_kw: float) -> float:
        b = self._settings.battery
        if action == Action.CHARGE:
            return min(abs(power_kw), b.max_charge_kw)
        if action == Action.DISCHARGE:
            return -min(abs(power_kw), b.max_discharge_kw)
        return 0.0

    async def _set_self_consumption(self) -> None:
        await self._select(self._ctl.forced_cmd_select, self._ctl.forced_stop_option)
        await self._select(self._ctl.ems_mode_select, self._ctl.ems_self_consumption_option)

    async def _select(self, entity_id: str, option: str) -> None:
        await self._client.call_service(
            "select", "select_option", {"entity_id": entity_id, "option": option}
        )

    async def shutdown(self) -> None:
        """Clean-exit path: leave the inverter in self-consumption mode."""
        log.info("executor shutdown: reverting inverter to self-consumption")
        try:
            await self._set_self_consumption()
        except Exception:  # noqa: BLE001 - best effort; HA-side watchdog is the backstop
            log.exception("could not revert EMS mode on shutdown")

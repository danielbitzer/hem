"""Plan execution: dry-run (default) or Sungrow register writes via HA.

SungrowExecutor guardrails (all of these hold regardless of what the plan says):
- never writes when the user's override input_boolean is on
- write-on-change only, rate-limited to control.max_writes_per_hour
- power setpoints clamped to the configured battery limits
- shutdown() re-asserts self-consumption mode (clean-exit path; the HA-side
  watchdog automation blueprint covers unclean death — see blueprints/)
"""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hem.config import Settings
from hem.ha.client import EntityNotFoundError, HaClient
from hem.models import Action, Plan

log = logging.getLogger(__name__)


class OverrideUnknown(Exception):
    """Raised when the override helper's state cannot be determined."""


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

    def check(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=1)
        while self._writes and self._writes[0] < cutoff:
            self._writes.popleft()
        return len(self._writes) < self._max

    def record(self, now: datetime | None = None) -> None:
        """Count a SUCCESSFUL write sequence — failed attempts don't consume
        the budget, so retries after transient errors aren't starved."""
        self._writes.append(now or datetime.now(UTC))


class SungrowExecutor:
    def __init__(self, client: HaClient, settings: Settings):
        self._client = client
        self._settings = settings
        self._ctl = settings.control.entities
        self._limiter = WriteRateLimiter(settings.control.max_writes_per_hour)
        self._last_applied: tuple[Action, float] | None = None

    async def _override_active(self) -> bool:
        """The user's kill-switch must FAIL CLOSED: only a genuinely missing
        helper means 'no override configured'. Any other error (timeout, HA
        restarting) blocks writes — a flaky GET doesn't imply the POSTs are
        safe, and the user may believe the override is holding us off."""
        try:
            state = await self._client.get_state(self._ctl.override_boolean)
        except EntityNotFoundError:
            return False
        except Exception as e:  # noqa: BLE001
            raise OverrideUnknown(f"could not read {self._ctl.override_boolean}: {e}") from e
        return state.state == "on"

    async def apply(self, plan: Plan) -> None:
        step0 = plan.intervals[0]
        action = step0.action
        power_kw = self._clamp_power(action, step0.power_kw, plan.live_spike)
        desired = (action, round(power_kw, 2))

        if desired == self._last_applied:
            return
        try:
            if await self._override_active():
                log.warning("override boolean is on: skipping inverter write")
                return
        except OverrideUnknown as e:
            log.warning("override state unknown (%s): failing closed, no write", e)
            return
        if not self._limiter.check():
            log.warning(
                "write rate limit reached (%d/h): holding previous inverter state",
                self._settings.control.max_writes_per_hour,
            )
            return

        log.info("inverter write: %s %.2f kW", action.value, power_kw)
        try:
            if action in (Action.IDLE, Action.CURTAIL):
                await self._set_self_consumption()
            else:
                # Order matters: set power and command REGISTERS first, engage
                # Forced mode LAST — a mid-sequence failure then leaves the
                # inverter still in its previous (safe) mode instead of Forced
                # mode with stale register values.
                await self._client.call_service(
                    "number",
                    "set_value",
                    {
                        "entity_id": self._ctl.forced_power_number,
                        "value": round(abs(power_kw) * 1000),  # mkaiser power number is W
                    },
                )
                cmd = (
                    self._ctl.forced_charge_option
                    if action == Action.CHARGE
                    else self._ctl.forced_discharge_option
                )
                await self._select(self._ctl.forced_cmd_select, cmd)
                await self._select(self._ctl.ems_mode_select, self._ctl.ems_forced_option)
        except Exception:
            # Best-effort local revert; does NOT consume rate budget (reverting
            # to safe must never be starved by the limiter).
            log.exception("inverter write sequence failed; attempting safe revert")
            with contextlib.suppress(Exception):
                await self._set_self_consumption()
            raise
        self._limiter.record()
        self._last_applied = desired

    def _clamp_power(self, action: Action, power_kw: float, live_spike: bool) -> float:
        b = self._settings.battery
        if action == Action.CHARGE:
            return min(abs(power_kw), b.max_charge_kw)
        if action == Action.DISCHARGE:
            cap = b.max_discharge_kw
            if live_spike:
                # confirmed spike: the raised cap is allowed (0 = disabled)
                cap = max(cap, self._settings.spike.discharge_kw)
            return -min(abs(power_kw), cap)
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

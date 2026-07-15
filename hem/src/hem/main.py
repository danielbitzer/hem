"""Entrypoint: 5-minute cycle scheduler + health/ingress web server.

Each cycle runs the full planner pipeline (gather -> solve -> publish -> record).
A WebSocket watcher triggers an early re-solve when the Amber price moves
significantly or the spike sensor flips — that's how a confirmed spike gets a
full-power discharge decision within seconds instead of at the next tick.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

import uvicorn
from dotenv import load_dotenv

from hem import __version__
from hem.adapters.amber import AmberExpressAdapter
from hem.adapters.solar import OpenMeteoSolarAdapter
from hem.adapters.sungrow import SungrowAdapter
from hem.adapters.weather import WeatherAdapter
from hem.config import Settings, load_settings, resolve_connection
from hem.executor import DryRunExecutor, Executor, SungrowExecutor
from hem.forecast.load import default_timezone
from hem.ha.client import HaClient
from hem.ha.publisher import Publisher
from hem.models import Plan
from hem.planner import InputsStale, Planner
from hem.recorder import Recorder, cycle_inputs_to_json
from hem.web.app import AppState, create_app

log = logging.getLogger("hem")

CYCLE_SECONDS = 300
WEB_PORT = 8099
EVENT_DEBOUNCE_S = 10
PRICE_TRIGGER_DELTA = 0.05  # $/kWh move that justifies an early re-solve
WS_RECONNECT_BACKOFF_S = 30


def seconds_to_next_boundary(now_epoch: float, period: int = CYCLE_SECONDS) -> float:
    """Seconds until the next wall-clock multiple of `period` (min 1s)."""
    return max(1.0, period - (now_epoch % period))


class PriceWatcher:
    """Triggers an asyncio.Event on significant price moves / spike changes."""

    def __init__(self, settings: Settings):
        ent = settings.entities
        self.watched = {e for e in (ent.buy_price, ent.sell_price, ent.price_spike) if e}
        self.trigger = asyncio.Event()
        self._last_seen: dict[str, str] = {}

    def on_change(self, entity_id: str, new_state: str) -> None:
        last = self._last_seen.get(entity_id)
        self._last_seen[entity_id] = new_state
        if last is None or last == new_state:
            return
        try:
            significant = abs(float(new_state) - float(last)) >= PRICE_TRIGGER_DELTA
        except ValueError:
            significant = True  # binary spike sensor or unavailable transitions
        if significant:
            log.info("price event: %s %s -> %s; early re-solve", entity_id, last, new_state)
            self.trigger.set()

    async def run(self, client: HaClient) -> None:
        while True:
            try:
                await client.watch_states(self.watched, self.on_change)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - reconnect forever; polling still works
                log.warning("price watcher down (%s); retry in %ss", e, WS_RECONNECT_BACKOFF_S)
                await asyncio.sleep(WS_RECONNECT_BACKOFF_S)


async def cycle(
    planner: Planner,
    publisher: Publisher,
    recorder: Recorder,
    settings: Settings,
    app_state: AppState,
    executor: Executor,
) -> Plan:
    now = datetime.now(UTC)
    data = await planner.gather(now)
    recorder.record("inputs", cycle_inputs_to_json(data), ts=now)
    plan = planner.optimize(data, now)
    planner.previous_plan = plan
    step0 = plan.intervals[0]
    recorder.record(
        "plan",
        {
            "action": step0.action.value,
            "power_kw": step0.power_kw,
            "objective_cost": plan.objective_cost,
            "solver_status": plan.solver_status,
            "solve_ms": plan.solve_ms,
        },
        ts=now,
    )
    await publisher.publish_plan(plan, settings.battery.capacity_kwh)
    await publisher.publish_status("ok", last_solve=now, solve_ms=plan.solve_ms)
    await executor.apply(plan)
    app_state.plan = plan
    log.info(
        "cycle ok: action=%s power=%+.2fkW soc=%.0f%% cost=$%.2f solve=%.0fms",
        step0.action.value,
        step0.power_kw,
        100 * data.battery.soc_frac,
        plan.objective_cost,
        plan.solve_ms,
    )
    return plan


async def run() -> None:
    load_dotenv()  # dev convenience: hem/.env with HEM_HA_URL/HEM_HA_TOKEN/HEM_OPTIONS_FILE
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    conn = resolve_connection()
    log.info(
        "HEM v%s starting (mode=%s, api=%s)", __version__, settings.control.mode, conn.rest_url
    )
    if settings.control.mode == "active":
        log.warning(
            "control.mode=active: HEM WILL WRITE TO THE INVERTER. "
            "Ensure the watchdog blueprint is installed and entity names verified."
        )

    app_state = AppState()
    web_config = uvicorn.Config(
        create_app(app_state), host="0.0.0.0", port=WEB_PORT, log_level="warning"
    )
    web_server = uvicorn.Server(web_config)
    web_task = asyncio.create_task(web_server.serve())

    recorder = Recorder()
    try:
        async with HaClient(conn) as client:
            if not await client.api_ok():
                log.warning("Home Assistant API not reachable yet; will retry each cycle")
            publisher = Publisher(client)
            executor: Executor = (
                SungrowExecutor(client, settings)
                if settings.control.mode == "active"
                else DryRunExecutor()
            )
            planner = Planner(
                settings,
                prices=AmberExpressAdapter(client, settings.entities),
                solar=OpenMeteoSolarAdapter(client, settings.entities),
                battery=SungrowAdapter(client, settings.entities, settings.battery),
                weather=WeatherAdapter(client, settings.entities),
                tz=default_timezone(),
            )
            watcher = PriceWatcher(settings)
            watcher_task = asyncio.create_task(watcher.run(client))

            try:
                while True:
                    try:
                        async with asyncio.timeout(90):
                            await cycle(planner, publisher, recorder, settings, app_state, executor)
                        app_state.health.mark_success()
                    except asyncio.CancelledError:
                        raise
                    except InputsStale as e:
                        log.warning("degraded: %s", e)
                        app_state.health.mark_error(str(e))
                        with contextlib.suppress(Exception):
                            await publisher.publish_status("degraded", detail=str(e))
                    except Exception as e:  # noqa: BLE001 - cycle must never kill the loop
                        log.exception("cycle failed")
                        app_state.health.mark_error(str(e))
                        with contextlib.suppress(Exception):
                            await publisher.publish_status("degraded", detail=str(e))

                    delay = seconds_to_next_boundary(time.time())
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout(delay):
                            await watcher.trigger.wait()
                            await asyncio.sleep(EVENT_DEBOUNCE_S)  # coalesce bursts
                        watcher.trigger.clear()
            finally:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task
                await executor.shutdown()
    finally:
        web_server.should_exit = True
        await web_task

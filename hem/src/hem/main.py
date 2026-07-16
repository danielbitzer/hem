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
import os
import signal
import time
from datetime import UTC, datetime

import uvicorn

from hem import __version__
from hem.adapters.amber import AmberExpressAdapter
from hem.adapters.solar import OpenMeteoSolarAdapter
from hem.adapters.sungrow import SungrowAdapter
from hem.adapters.weather import WeatherAdapter
from hem.config import EnvSettings, Settings, load_settings, resolve_connection
from hem.forecast.load import build_load_forecaster, default_timezone
from hem.ha.client import HaClient
from hem.ha.publisher import Publisher
from hem.models import Plan
from hem.optimizer.model import SolverError
from hem.planner import InputsStale, Planner
from hem.web.app import AppState, create_app

log = logging.getLogger("hem")

CYCLE_SECONDS = 300
WEB_PORT = 8099
EVENT_DEBOUNCE_S = 2  # buy+sell arrive together; just soak up that burst
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

    def on_change(self, entity_id: str, new_state: str, old_state: str | None = None) -> None:
        # seed from the event's own old_state so the FIRST change after a
        # (re)connect can still trigger — a spike confirming minutes after an
        # add-on restart must not wait for the 5-min boundary
        last = self._last_seen.get(entity_id) or old_state
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
    settings: Settings,
    app_state: AppState,
) -> Plan:
    now = datetime.now(UTC)
    data = await planner.gather(now)
    # The solve is synchronous CVXPY/HiGHS — run off the event loop so /health,
    # the WS watcher, and the dashboard stay responsive during long solves.
    try:
        plan = await asyncio.to_thread(planner.optimize, data, now)
    except SolverError as e:
        # a re-raise here means no previous plan to reuse either — that IS a
        # failed cycle (degraded status via the caller's handler)
        log.error("solver failed: %s; falling back to the previous plan", e)
        plan = planner.fallback(now)
    planner.previous_plan = plan
    step0 = plan.intervals[0]
    forecast_end = data.price_forecast_end.isoformat() if data.price_forecast_end else None
    # plan and meta go to the dashboard together — meta lagging the plan gave
    # first-poll renders no capacity axis and no load-forecast warning
    app_state.plan = plan
    app_state.meta = {
        "capacity_kwh": settings.battery.capacity_kwh,
        "price_forecast_end": forecast_end,
        "coverage": data.coverage,
        "load_forecast": data.load_forecast_status,
        "load_forecast_info": data.load_forecast_info,
    }

    # Publishing IS the output: the user's actuator automation (see
    # blueprints/hem_actuator.yaml) turns these sensors into inverter control.
    await publisher.publish_plan(plan, settings.battery.capacity_kwh)
    await publisher.publish_status(
        "ok",
        last_solve=now,
        solve_ms=plan.solve_ms,
        extra={
            "coverage": data.coverage,
            "price_forecast_end": forecast_end,
            "load_forecast": data.load_forecast_status,
        },
    )

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
    env = EnvSettings()  # HEM_* env vars, plus ./.env in dev
    settings = load_settings(env.options_file)
    logging.basicConfig(
        level=(env.log_level or settings.log_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    conn = resolve_connection(env)
    log.info("HEM v%s starting (api=%s)", __version__, conn.rest_url)

    if os.environ.get("SUPERVISOR_TOKEN"):
        log.info("dashboard: HA sidebar -> Energy Manager (ingress)")
    else:
        log.info("dashboard: http://localhost:%d", WEB_PORT)

    app_state = AppState()
    web_task = asyncio.create_task(_serve_web(app_state))

    # uvicorn's serve() captures SIGTERM/SIGINT and RE-RAISES them with default
    # handlers after its graceful stop — killing the process before our finally
    # blocks can run. Own handlers, installed after the web task starts, win:
    # they cancel this task so shutdown is clean and logged.
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    assert main_task is not None
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, main_task.cancel)

    try:
        async with HaClient(conn) as client:
            if not await client.api_ok():
                log.warning("Home Assistant API not reachable yet; will retry each cycle")
            publisher = Publisher(client)
            tz = default_timezone()
            planner = Planner(
                settings,
                prices=AmberExpressAdapter(client, settings.entities),
                solar=OpenMeteoSolarAdapter(client, settings.entities),
                battery=SungrowAdapter(client, settings.entities, settings.battery),
                weather=WeatherAdapter(client, settings.entities),
                tz=tz,
                load_forecaster=build_load_forecaster(
                    client,
                    settings.entities.load_power,
                    tz,
                    outdoor_temp=settings.entities.outdoor_temp,
                ),
            )
            watcher = PriceWatcher(settings)
            watcher_task = asyncio.create_task(watcher.run(client))

            try:
                while True:
                    try:
                        async with asyncio.timeout(90):
                            await cycle(planner, publisher, settings, app_state)
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
                    try:
                        async with asyncio.timeout(delay):
                            await watcher.trigger.wait()
                            # trigger observed: clear BEFORE the debounce so a
                            # boundary timeout during the sleep can't leave the
                            # event set and cause a spurious extra re-solve
                            watcher.trigger.clear()
                            await asyncio.sleep(EVENT_DEBOUNCE_S)  # coalesce bursts
                    except TimeoutError:
                        watcher.trigger.clear()
            finally:
                watcher_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher_task
    except asyncio.CancelledError:
        log.info("shutting down (signal received)")
    finally:
        web_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await web_task


async def _serve_web(app_state: AppState) -> None:
    """Run the dashboard; planning must survive its failure (e.g. port bound —
    uvicorn raises SystemExit(3) inside the task)."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(app_state), host="0.0.0.0", port=WEB_PORT, log_level="warning")
    )
    try:
        await server.serve()
        log.warning("web server exited; dashboard/health unavailable")
    except asyncio.CancelledError:
        server.should_exit = True
        raise
    except (SystemExit, Exception) as e:  # noqa: BLE001 - dashboard is not load-bearing
        log.error("web server failed (%s); continuing without dashboard/health", e)

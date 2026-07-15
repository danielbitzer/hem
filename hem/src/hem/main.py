"""Entrypoint: 5-minute cycle scheduler + health/ingress web server.

Phase 0 cycle: read the configured battery SoC entity and publish
sensor.hem_status — proves connectivity in both add-on and standalone modes.
The planner pipeline replaces the cycle body in Phase 2.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

import uvicorn

from hem import __version__
from hem.config import Settings, load_settings, resolve_connection
from hem.ha.client import HaClient
from hem.ha.publisher import Publisher
from hem.web.app import HealthState, create_app

log = logging.getLogger("hem")

CYCLE_SECONDS = 300
WEB_PORT = 8099


def seconds_to_next_boundary(now_epoch: float, period: int = CYCLE_SECONDS) -> float:
    """Seconds until the next wall-clock multiple of `period` (min 1s)."""
    return max(1.0, period - (now_epoch % period))


async def cycle(client: HaClient, publisher: Publisher, settings: Settings) -> None:
    soc_state = await client.get_state(settings.entities.battery_soc)
    if soc_state.available:
        unit = soc_state.attributes.get("unit_of_measurement", "")
        detail = f"battery_soc={soc_state.state}{unit}"
    else:
        detail = f"{settings.entities.battery_soc} is {soc_state.state}"
    log.info("cycle ok: %s", detail)
    await publisher.publish_status("ok", last_solve=datetime.now(UTC), detail=detail)


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    conn = resolve_connection()
    log.info(
        "HEM v%s starting (mode=%s, api=%s)", __version__, settings.control.mode, conn.rest_url
    )

    health = HealthState()
    web_config = uvicorn.Config(
        create_app(health), host="0.0.0.0", port=WEB_PORT, log_level="warning"
    )
    web_server = uvicorn.Server(web_config)
    web_task = asyncio.create_task(web_server.serve())

    try:
        async with HaClient(conn) as client:
            if not await client.api_ok():
                log.warning("Home Assistant API not reachable yet; will retry each cycle")
            publisher = Publisher(client)

            while True:
                try:
                    async with asyncio.timeout(60):
                        await cycle(client, publisher, settings)
                    health.mark_success()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - cycle must never kill the loop
                    log.exception("cycle failed")
                    health.mark_error(str(e))
                    try:
                        await publisher.publish_status("degraded", detail=str(e))
                    except Exception:  # noqa: BLE001
                        log.warning("could not publish degraded status")

                await asyncio.sleep(seconds_to_next_boundary(time.time()))
    finally:
        web_server.should_exit = True
        await web_task

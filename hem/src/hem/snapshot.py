"""Phase 1 verification CLI: fetch all live inputs, build the aligned grid,
and print it as a table for eyeballing against HA's entity attributes.

    HEM_HA_URL=... HEM_HA_TOKEN=... HEM_OPTIONS_FILE=./dev-options.json \
        uv run python -m hem.snapshot
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from hem.adapters.amber import AmberExpressAdapter
from hem.adapters.solar import OpenMeteoSolarAdapter
from hem.adapters.sungrow import SungrowAdapter
from hem.adapters.weather import WeatherAdapter, WeatherParseError
from hem.config import load_settings, resolve_connection
from hem.forecast.load import BaselineLoadForecaster, default_timezone
from hem.ha.client import HaClient
from hem.timegrid import TimeGrid, coverage, resample_mean, resample_previous


async def main() -> None:
    settings = load_settings()
    conn = resolve_connection()
    tz = default_timezone()
    now = datetime.now(UTC)

    async with HaClient(conn) as client:
        amber = AmberExpressAdapter(client, settings.entities)
        solar = OpenMeteoSolarAdapter(client, settings.entities)
        sungrow = SungrowAdapter(client, settings.entities, settings.battery)
        weather = WeatherAdapter(client, settings.entities)

        prices, pv, battery = await asyncio.gather(
            amber.get_prices(), solar.get_pv(), sungrow.get_battery_state()
        )
        try:
            temps_series = await weather.get_temperature_forecast()
        except WeatherParseError as e:
            print(f"warning: no temperature forecast ({e}); load rules disabled")
            temps_series = None

    horizon = timedelta(hours=settings.optimizer.horizon_hours)
    grid = TimeGrid.build(now, prices.sell.times, horizon)

    buy = resample_previous(prices.buy, grid)
    sell = resample_previous(prices.sell, grid)
    buy[0], sell[0] = prices.current_buy, prices.current_sell
    pv_kw = resample_mean(pv, grid)
    temps = resample_previous(temps_series, grid) if temps_series else None
    load_kw = BaselineLoadForecaster(settings.load_profile, tz).forecast(grid, temps)

    print(f"now={now.isoformat()}  local tz={tz}  steps={len(grid)}")
    print(
        f"battery: soc={battery.soc_frac:.1%} power={battery.power_kw:+.2f}kW "
        f"capacity={battery.capacity_kwh}kWh  live_spike={prices.live_spike}"
    )
    print(
        f"coverage: buy={coverage(prices.buy, grid):.0%} sell={coverage(prices.sell, grid):.0%} "
        f"pv={coverage(pv, grid):.0%}"
        + (f" temps={coverage(temps_series, grid):.0%}" if temps_series else " temps=n/a")
    )
    header = f"{'local start':<17}{'dt(m)':>6}{'buy':>8}{'sell':>8}{'pv kW':>8}{'load kW':>9}"
    header += f"{'temp C':>8}" if temps is not None else ""
    print(header)
    for i, step in enumerate(grid.steps):
        row = (
            f"{step.start.astimezone(tz).strftime('%a %d %H:%M'):<17}"
            f"{step.dt_hours * 60:>6.0f}{buy[i]:>8.4f}{sell[i]:>8.4f}"
            f"{pv_kw[i]:>8.2f}{load_kw[i]:>9.2f}"
        )
        if temps is not None:
            row += f"{temps[i]:>8.1f}"
        print(row)


if __name__ == "__main__":
    asyncio.run(main())

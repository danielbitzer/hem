from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from conftest import FakeHa, fake_ha_client

from hem.adapters.amber import AmberExpressAdapter
from hem.adapters.solar import OpenMeteoSolarAdapter
from hem.adapters.sungrow import SungrowAdapter
from hem.adapters.weather import WeatherAdapter
from hem.config import Settings
from hem.models import Action, BatteryState, Plan, PlanInterval, PriceForecast, Series
from hem.optimizer.model import OptimizerInputs
from hem.planner import CycleData, InputsStale, Planner
from hem.timegrid import TimeGrid

ADELAIDE = ZoneInfo("Australia/Adelaide")
# Mid-fixture-era: after the general price interval start (11:35Z), within
# staleness windows of both price sensors.
NOW = datetime(2026, 7, 15, 11, 36, 30, tzinfo=UTC)

SETTINGS_DICT = {
    "entities": {
        "buy_price": "sensor.amber_express_general_price",
        "sell_price": "sensor.amber_express_feed_in_price",
        "price_spike": "binary_sensor.amber_express_price_spike",
        "pv_forecast_today": "sensor.home_energy_production_today",
        "pv_forecast_tomorrow": "sensor.home_energy_production_tomorrow",
        "battery_soc": "sensor.battery_level",
        "battery_power": "sensor.battery_power",
        "weather": "weather.henley_beach_hourly",
    },
    "battery": {"capacity_kwh": 12.8, "max_charge_kw": 5.0, "max_discharge_kw": 5.0},
    "grid": {"import_limit_kw": 15.0, "export_limit_kw": 5.0},
    "load_profile": {
        "weekday_kw": [0.5] * 24,
        "weekend_kw": [0.6] * 24,
        "temp_rules": [{"when": "temp_below", "threshold_c": 12.0, "add_kw": 1.2}],
    },
}


def make_settings(**overrides) -> Settings:
    base = {**SETTINGS_DICT, **overrides}
    return Settings.model_validate(base)


def add_battery_states(
    fake: FakeHa,
    soc: str = "72.5",
    power: str = "-1200",
    ts: str = "2026-07-15T11:35:00+00:00",
) -> None:
    fake.states["sensor.battery_level"] = {
        "entity_id": "sensor.battery_level",
        "state": soc,
        "attributes": {"unit_of_measurement": "%"},
        "last_updated": ts,
    }
    fake.states["sensor.battery_power"] = {
        "entity_id": "sensor.battery_power",
        "state": power,
        "attributes": {"unit_of_measurement": "W"},
        "last_updated": ts,
    }


def full_fake_ha() -> FakeHa:
    fake = FakeHa()
    for name in (
        "amber_express_feed_in_price",
        "amber_express_general_price",
        "amber_express_price_spike",
        "solar_production_today",
        "solar_production_tomorrow",
        "weather_henley_beach_hourly",
    ):
        fake.add_fixture(name)
    add_battery_states(fake)
    fake.service_responses[("weather", "get_forecasts")] = {
        "weather.henley_beach_hourly": {
            "forecast": [
                {"datetime": "2026-07-15T21:00:00+09:30", "temperature": 6.5},
                {"datetime": "2026-07-16T00:00:00+09:30", "temperature": 5.0},
                {"datetime": "2026-07-16T09:00:00+09:30", "temperature": 12.5},
            ]
        }
    }
    return fake


def make_planner(client, settings: Settings) -> Planner:
    return Planner(
        settings,
        prices=AmberExpressAdapter(client, settings.entities),
        solar=OpenMeteoSolarAdapter(client, settings.entities),
        battery=SungrowAdapter(client, settings.entities, settings.battery),
        weather=WeatherAdapter(client, settings.entities),
        tz=ADELAIDE,
    )


async def test_full_cycle_against_fixtures():
    settings = make_settings()
    fake = full_fake_ha()
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        plan = await planner.run_cycle(NOW)

    assert plan.solver_status in ("optimal", "optimal_inaccurate")
    assert plan.intervals[0].start == NOW
    assert plan.intervals[-1].end == NOW + timedelta(hours=36)
    # Step 0 uses the live sensor states, not the forecast attribute
    assert plan.intervals[0].buy == pytest.approx(0.44)
    # Cold night (6.5C < 12C rule) -> load = 0.5 baseline + 1.2 heating
    assert plan.intervals[0].load_kw == pytest.approx(1.7)
    # Battery at 72.5%: tomorrow evening's high prices should provoke export at
    # some point in the horizon
    assert any(iv.action == Action.DISCHARGE for iv in plan.intervals)


async def test_stale_battery_raises():
    settings = make_settings()
    fake = full_fake_ha()
    # Prices fresh (11:35), battery last updated 25 min before NOW -> stale
    add_battery_states(fake, ts="2026-07-15T11:11:00+00:00")
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        with pytest.raises(InputsStale, match="battery"):
            await planner.gather(NOW)


async def test_spike_reserve_armed_from_forecast():
    settings = make_settings(
        spike={"lookahead_hours": 48, "reserve_kwh": 6.0, "high_price_threshold": 0.6}
    )
    fake = full_fake_ha()
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        data = await planner.gather(NOW)
    reserve = data.inputs.reserve_kwh
    assert reserve is not None
    assert reserve.max() == pytest.approx(6.0)
    assert reserve[0] == pytest.approx(6.0)  # held from now...
    assert reserve[-1] == 0.0  # ...released at/after the trigger step


def synthetic_cycle_data(settings: Settings, live_spike: bool = False) -> CycleData:
    T = 12
    start = NOW
    bounds = [start + timedelta(minutes=30 * i) for i in range(1, T)]
    grid = TimeGrid.build(start, bounds, timedelta(hours=6))
    inputs = OptimizerInputs(
        dt_hours=grid.dt_hours,
        buy=np.full(len(grid), 0.30),
        sell=np.full(len(grid), 0.10),
        pv=np.zeros(len(grid)),
        load=np.full(len(grid), 0.5),
        soc0_kwh=6.4,
    )
    series = Series(times=[start], values=[0.30])
    prices = PriceForecast(
        buy=series, sell=series, current_buy=0.30, current_sell=0.10, live_spike=live_spike
    )
    battery = BatteryState(soc_frac=0.5, power_kw=0.0, capacity_kwh=12.8, ts=start)
    return CycleData(grid=grid, inputs=inputs, prices=prices, battery=battery, temps=None)


def previous_plan_with(action: Action) -> Plan:
    iv = PlanInterval(
        start=NOW - timedelta(minutes=5),
        end=NOW + timedelta(minutes=25),
        action=action,
        power_kw=0.0,
        soc_start=6.4,
        soc_end=6.4,
        buy=0.30,
        sell=0.10,
        pv_kw=0.0,
        load_kw=0.5,
        grid_import_kw=0.5,
        grid_export_kw=0.0,
        interval_cost=0.075,
    )
    return Plan(
        intervals=[iv],
        objective_cost=0.0,
        solver_status="optimal",
        solve_ms=1.0,
        computed_at=NOW - timedelta(minutes=5),
    )


def offline_planner(settings: Settings) -> Planner:
    # optimize()/hysteresis don't touch the adapters, so dummies are fine here
    return Planner(
        settings,
        prices=cast(AmberExpressAdapter, None),
        solar=cast(OpenMeteoSolarAdapter, None),
        battery=cast(SungrowAdapter, None),
        weather=cast(WeatherAdapter, None),
        tz=ADELAIDE,
    )


def test_hysteresis_keeps_near_degenerate_previous_action():
    """Flat prices: discharge-for-load vs idle differ by well under the $0.02
    threshold, so the previous action (idle) is kept."""
    settings = make_settings(
        optimizer={"action_switch_threshold_dollars": 0.05, "forecast_haircut": 0.0}
    )
    planner = offline_planner(settings)
    data = synthetic_cycle_data(settings)
    planner.previous_plan = previous_plan_with(Action.IDLE)
    plan = planner.optimize(data, NOW)
    assert plan.intervals[0].action == Action.IDLE
    assert "hysteresis" in plan.solver_status


def test_hysteresis_disabled_switches_freely():
    settings = make_settings(
        optimizer={"action_switch_threshold_dollars": 0.0, "forecast_haircut": 0.0}
    )
    planner = offline_planner(settings)
    data = synthetic_cycle_data(settings)
    planner.previous_plan = previous_plan_with(Action.IDLE)
    plan = planner.optimize(data, NOW)
    assert plan.intervals[0].action == Action.DISCHARGE  # self-consumption wins


def test_live_spike_guard_suppresses_grid_charge():
    settings = make_settings(optimizer={"action_switch_threshold_dollars": 0.0})
    planner = offline_planner(settings)
    data = synthetic_cycle_data(settings, live_spike=True)
    data.inputs.buy[0] = -0.10  # would normally trigger a grid charge now
    plan = planner.optimize(data, NOW)
    assert plan.intervals[0].action != Action.CHARGE


def test_fallback_shifts_previous_plan():
    settings = make_settings()
    planner = offline_planner(settings)
    planner.previous_plan = previous_plan_with(Action.IDLE)
    fallback = planner._fallback(NOW)
    assert fallback.solver_status.startswith("stale")
    assert all(iv.end > NOW for iv in fallback.intervals)

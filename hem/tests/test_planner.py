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
    # profile mode: hourly baseline only (temperature sensitivity is
    # history+outdoor_temp's job)
    assert plan.intervals[0].load_kw == pytest.approx(0.5)
    # Battery at 72.5%: tomorrow evening's high prices should provoke export at
    # some point in the horizon
    assert any(iv.action == Action.DISCHARGE for iv in plan.intervals)


async def test_old_battery_report_is_tolerated():
    """mkaiser battery sensors only report on value change, so an old
    last_reported must NOT abort the cycle (idle battery == constant SoC).
    Unavailable battery sensors are still fatal (adapter raises)."""
    settings = make_settings()
    fake = full_fake_ha()
    add_battery_states(fake, ts="2026-07-15T09:00:00+00:00")  # 2.5h old
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        data = await planner.gather(NOW)
    assert data.battery.soc_frac == pytest.approx(0.725)


async def test_stale_prices_still_fatal():
    settings = make_settings()
    fake = full_fake_ha()
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        late = NOW + timedelta(hours=2)  # price sensors reported 11:35
        with pytest.raises(InputsStale, match="prices"):
            await planner.gather(late)


async def test_unchanged_but_reported_battery_is_fresh():
    """HA only bumps last_updated when the VALUE changes; a battery sitting at
    a constant SoC must not be treated as stale while last_reported is fresh.
    (Regression: live loop wrongly went degraded after ~10 min of flat SoC.)"""
    settings = make_settings()
    fake = full_fake_ha()
    add_battery_states(fake, ts="2026-07-15T10:00:00+00:00")  # value unchanged for 1.5h
    for entity in ("sensor.battery_level", "sensor.battery_power"):
        fake.states[entity]["last_reported"] = "2026-07-15T11:36:00+00:00"  # polled 30s ago
    async with fake_ha_client(fake) as client:
        planner = make_planner(client, settings)
        data = await planner.gather(NOW)
    assert data.battery.soc_frac == pytest.approx(0.725)


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


def test_spike_reserve_vector_lookahead_and_trigger():
    from hem.planner import spike_reserve_vector

    dt = np.full(12, 0.5)  # 6h of 30-min steps
    sell = np.full(12, 0.10)
    sell[6] = 5.0  # 3h out, inside 4h lookahead
    reserve = spike_reserve_vector(
        sell, dt, lookahead_hours=4, high_price_threshold=1.0, reserve_kwh=6.0, soc_max_kwh=44.8
    )
    assert reserve is not None
    assert (reserve[:6] == 6.0).all()
    assert (reserve[6:] == 0.0).all()

    sell2 = np.full(12, 0.10)
    sell2[10] = 5.0  # 5h out, beyond lookahead
    assert (
        spike_reserve_vector(
            sell2, dt, lookahead_hours=4, high_price_threshold=1.0, reserve_kwh=6.0,
            soc_max_kwh=44.8,
        )
        is None
    )


def test_backtest_hem_policy_arms_reserve():
    """The backtester must run the SAME spike reserve as the live planner."""

    from hem.backtest.policies import HemPolicy
    from hem.backtest.sim import CycleRecord
    from hem.config import Spike
    from hem.optimizer.model import GridParams
    from hem.planner import battery_params

    settings = make_settings(
        battery={"capacity_kwh": 44.8, "max_charge_kw": 12.0, "max_discharge_kw": 12.0}
    )
    battery = battery_params(settings)
    grid_params = GridParams(import_limit_kw=15.0, export_limit_kw=15.0)
    T = 12
    sell = np.full(T, 0.50)  # attractive enough to sell down without a reserve
    buy = np.full(T, 0.60)
    sell[6], buy[6] = 5.0, 5.3  # potential spike 3h out
    record = CycleRecord(
        ts=NOW,
        dt_hours=np.full(T, 0.5),
        buy=buy,
        sell=sell,
        pv=np.zeros(T),
        load=np.full(T, 0.5),
        current_buy=0.60,
        current_sell=0.50,
        live_spike=False,
    )
    spike_cfg = Spike(lookahead_hours=4, reserve_kwh=20.0, high_price_threshold=1.0,
                      reserve_penalty_per_kwh=5.0, discharge_kw=15.0)
    policy = HemPolicy(battery, grid_params, spike=spike_cfg)
    inputs = policy.build_inputs(record, soc_kwh=30.0)
    # Structural parity: the reserve/cap vectors are exactly what the shared
    # planner functions produce for the same arrays
    from hem.planner import discharge_cap_vector, spike_reserve_vector

    expected_reserve = spike_reserve_vector(
        sell, record.dt_hours, lookahead_hours=4, high_price_threshold=1.0,
        reserve_kwh=20.0, soc_max_kwh=battery.soc_max_kwh,
    )
    assert expected_reserve is not None
    assert inputs.reserve_kwh is not None
    assert (inputs.reserve_kwh == expected_reserve).all()
    assert (inputs.reserve_kwh[:6] == 20.0).all()  # armed up to the spike step
    # No live spike in this record -> no raised discharge cap
    assert inputs.max_discharge_kw_step is None
    live = CycleRecord(**{**record.__dict__, "live_spike": True})
    caps = policy.build_inputs(live, soc_kwh=30.0).max_discharge_kw_step
    expected_caps = discharge_cap_vector(T, True, 15.0, battery.max_discharge_kw)
    assert caps is not None and expected_caps is not None
    assert (caps == expected_caps).all()


def test_fallback_shifts_previous_plan():
    settings = make_settings()
    planner = offline_planner(settings)
    planner.previous_plan = previous_plan_with(Action.IDLE)
    fallback = planner._fallback(NOW)
    assert fallback.solver_status.startswith("stale")
    assert all(iv.end > NOW for iv in fallback.intervals)

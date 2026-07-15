"""Backtest simulator: physics conservation + HEM must beat the baselines."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hem.backtest.policies import HemPolicy, NoBatteryPolicy, SelfConsumptionPolicy
from hem.backtest.sim import BatterySim, CycleRecord, simulate
from hem.optimizer.model import BatteryParams, GridParams

BATTERY = BatteryParams(
    capacity_kwh=12.8,
    max_charge_kw=5.0,
    max_discharge_kw=5.0,
    efficiency_charge=0.95,
    efficiency_discharge=0.95,
    soc_min_kwh=1.28,
    soc_max_kwh=12.8,
    wear_cost_per_kwh=0.04,
    allow_grid_charge=True,
)
GRID = GridParams(import_limit_kw=15.0, export_limit_kw=5.0)
START = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)


def test_battery_sim_energy_conservation():
    sim = BatterySim(BATTERY, soc_kwh=6.4)
    rng = np.random.default_rng(42)
    for _ in range(200):
        sim.apply(float(rng.uniform(-6, 6)), 0.25)
    expected = (
        6.4
        + BATTERY.efficiency_charge * sim.charged_kwh
        - sim.discharged_kwh / BATTERY.efficiency_discharge
    )
    assert sim.soc_kwh == pytest.approx(expected, abs=1e-9)
    assert BATTERY.soc_min_kwh <= sim.soc_kwh <= BATTERY.soc_max_kwh


def test_battery_sim_clamps_at_bounds():
    sim = BatterySim(BATTERY, soc_kwh=12.7)
    achieved = sim.apply(5.0, 1.0)  # only 0.1 kWh of headroom
    assert achieved == pytest.approx(0.1 / BATTERY.efficiency_charge, abs=1e-6)
    assert sim.soc_kwh == pytest.approx(12.8)
    sim2 = BatterySim(BATTERY, soc_kwh=1.4)
    achieved = sim2.apply(-5.0, 1.0)
    assert sim2.soc_kwh == pytest.approx(BATTERY.soc_min_kwh)


def spike_day_records() -> list[CycleRecord]:
    """A synthetic day at 30-min cadence with an evening price spike and a
    midday PV hump. Each record sees the remaining actuals as its forecast
    (perfect foresight — an upper bound on HEM, fine for a mechanics test)."""
    T = 48
    buy = np.full(T, 0.30)
    sell = np.full(T, 0.10)
    buy[36:38], sell[36:38] = 5.3, 5.0  # 18:00-19:00 spike
    hours = np.arange(T) * 0.5
    pv = np.clip(6.0 * np.sin((hours - 7) / 10 * np.pi), 0, None)  # ~7:00-17:00
    load = np.full(T, 1.0)
    records = []
    for i in range(T):
        records.append(
            CycleRecord(
                ts=START + timedelta(minutes=30 * i),
                dt_hours=np.full(T - i, 0.5),
                buy=buy[i:].copy(),
                sell=sell[i:].copy(),
                pv=pv[i:].copy(),
                load=load[i:].copy(),
                current_buy=float(buy[i]),
                current_sell=float(sell[i]),
                live_spike=bool(sell[i] >= 1.0),
            )
        )
    return records


def test_hem_beats_baselines_on_spike_day():
    records = spike_day_records()
    results = {
        p.name: simulate(p, records, BATTERY, GRID.export_limit_kw, soc0_kwh=6.4)
        for p in (NoBatteryPolicy(), SelfConsumptionPolicy(), HemPolicy(BATTERY, GRID))
    }
    hem = results["hem"].total_cost
    selfc = results["self-consumption"].total_cost
    nobatt = results["no-battery"].total_cost
    assert hem < selfc < nobatt
    # The spike is the payday: HEM must earn real revenue during it
    assert results["hem"].spike_revenue() > 4.0
    assert results["self-consumption"].spike_revenue() == pytest.approx(0.0, abs=0.5)


def test_self_consumption_never_trades():
    records = spike_day_records()
    result = simulate(SelfConsumptionPolicy(), records, BATTERY, GRID.export_limit_kw, 6.4)
    # only exports when PV exceeds load AND battery is full; never imports to charge
    for step, rec in zip(result.steps, records, strict=False):
        if step.battery_kw > 0.01:  # charging comes from PV surplus only
            assert rec.pv[0] > rec.load[0]


def test_cycle_record_json_roundtrip():
    record = {
        "ts": "2026-07-15T11:36:30+00:00",
        "kind": "inputs",
        "data": {
            "dt_hours": [0.5, 0.5],
            "buy": [0.44, 0.42],
            "sell": [0.16, 0.14],
            "pv_kw": [0.0, 0.0],
            "load_kw": [1.7, 1.7],
            "prices": {"current_buy": 0.44, "current_sell": 0.1585, "live_spike": False},
        },
    }
    parsed = CycleRecord.from_json(record)
    assert parsed.current_buy == 0.44
    assert parsed.buy.tolist() == [0.44, 0.42]
    assert parsed.ts.tzinfo is not None

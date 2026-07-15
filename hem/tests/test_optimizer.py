"""Qualitative MILP scenarios: the behaviors the money depends on."""

import numpy as np
import pytest

from hem.optimizer.model import (
    BatteryParams,
    GridParams,
    OptimizerConfig,
    OptimizerInputs,
    auto_terminal_value,
    solve,
)

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


def make_inputs(
    T: int = 24,
    dt: float = 0.5,
    buy: float | np.ndarray = 0.30,
    sell: float | np.ndarray = 0.10,
    pv: float | np.ndarray = 0.0,
    load: float | np.ndarray = 0.5,
    soc0: float = 6.4,
    reserve: np.ndarray | None = None,
) -> OptimizerInputs:
    def full(v) -> np.ndarray:
        return np.full(T, float(v)) if np.isscalar(v) else np.asarray(v, dtype=float)

    return OptimizerInputs(
        dt_hours=np.full(T, dt),
        buy=full(buy),
        sell=full(sell),
        pv=full(pv),
        load=full(load),
        soc0_kwh=soc0,
        reserve_kwh=reserve,
    )


def config(terminal_value: float, reserve_penalty: float = 0.5) -> OptimizerConfig:
    return OptimizerConfig(
        terminal_value=terminal_value,
        reserve_penalty_per_kwh=reserve_penalty,
        solver_timeout_s=30,
    )


def test_scenario_price_spike_precharge_then_full_export():
    """Evening sell spike -> charge beforehand, dump at the export limit during.

    Realistic Amber spike: BOTH prices are high in the spike interval (buy =
    spot + network > sell = spot); the arbitrage is cross-interval — charge
    cheap earlier, export during the spike.
    """
    sell = np.full(24, 0.10)
    buy = np.full(24, 0.30)
    sell[10:12] = 5.0  # 1h spike
    buy[10:12] = 5.3
    inputs = make_inputs(buy=buy, sell=sell, soc0=2.0)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.25))
    # Grid-charges before the spike (buy 0.30 << sell 5.00 covers losses)
    assert sol.charge_kw[:10].sum() > 5.0
    # Discharges at max during the spike; export = discharge - house load
    # (importing at 5.30 to pad the 5.00 export would lose money)
    assert sol.discharge_kw[10] == pytest.approx(BATTERY.max_discharge_kw, abs=0.01)
    assert sol.discharge_kw[11] == pytest.approx(BATTERY.max_discharge_kw, abs=0.01)
    expected_export = BATTERY.max_discharge_kw - 0.5
    assert sol.grid_export_kw[10] == pytest.approx(expected_export, abs=0.01)
    # Never imports during the spike intervals
    assert sol.grid_import_kw[10:12].max() < 0.01


def test_scenario_negative_buy_price_grid_charges():
    """Negative overnight prices -> get paid to charge the battery."""
    buy = np.full(24, 0.30)
    buy[0:6] = -0.05
    inputs = make_inputs(buy=buy, sell=0.02, soc0=2.0)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.25))
    assert sol.charge_kw[0:6].sum() > 4.0
    assert sol.grid_import_kw[0:6].max() > 1.0


def test_scenario_negative_feed_in_curtails_solar():
    """Negative sell price + full battery -> spill excess PV rather than pay to export."""
    inputs = make_inputs(buy=0.25, sell=-0.10, pv=5.0, load=0.5, soc0=12.8)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.20))
    assert sol.grid_export_kw.max() < 0.01  # never pays to export
    assert sol.pv_used_kw.max() < 1.0  # curtailed down to ~load
    assert sol.grid_import_kw.max() < 0.01  # load still served by PV


def test_scenario_flat_prices_no_arbitrage_churn():
    """Flat prices -> no grid charging, no export; at most self-consumption."""
    inputs = make_inputs(buy=0.30, sell=0.10, load=1.0, soc0=6.4)
    terminal = auto_terminal_value(inputs.buy, BATTERY)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=terminal))
    assert sol.charge_kw.max() < 0.01
    assert sol.grid_export_kw.max() < 0.01


def test_scenario_terminal_value_prevents_horizon_drain():
    """Without terminal value the battery dumps at any positive sell price by
    horizon end; with it, residual energy is held."""
    inputs = make_inputs(T=12, buy=0.30, sell=0.10, load=0.5, soc0=10.0)
    drained = solve(inputs, BATTERY, GRID, config(terminal_value=0.0))
    held = solve(
        inputs, BATTERY, GRID, config(terminal_value=auto_terminal_value(inputs.buy, BATTERY))
    )
    assert drained.soc_kwh[-1] == pytest.approx(BATTERY.soc_min_kwh, abs=0.05)
    # Self-consumption discharge (load 0.5 kW x 6 h) is fine; exporting the
    # residual at sell=0.10 is not.
    assert held.soc_kwh[-1] > 6.5
    assert held.grid_export_kw.max() < 0.01


def test_scenario_spike_reserve_holds_energy():
    """Soft reserve floor keeps SoC available for a potential spike, unless
    the penalty is outweighed (it isn't here)."""
    # Attractive early sell price + low terminal value would normally drain it
    inputs_free = make_inputs(buy=0.60, sell=0.50, soc0=10.0)
    free = solve(inputs_free, BATTERY, GRID, config(terminal_value=0.05))
    assert free.soc_kwh[-1] == pytest.approx(BATTERY.soc_min_kwh, abs=0.05)

    reserve = np.full(24, 6.0)
    inputs_held = make_inputs(buy=0.60, sell=0.50, soc0=10.0, reserve=reserve)
    held = solve(inputs_held, BATTERY, GRID, config(terminal_value=0.05, reserve_penalty=5.0))
    assert held.soc_kwh[1:].min() >= 6.0 - 0.05


def test_scenario_spike_reserve_yields_to_bigger_opportunity():
    """The reserve is soft: a confirmed spike RIGHT NOW (no time to pre-charge)
    is worth more than the slack penalty, so the floor is broken to sell.

    (With any lead time the optimizer prefers to grid-charge first and keep
    the reserve intact — verified by the passing pre-charge scenario above.)
    """
    sell = np.full(24, 0.10)
    buy = np.full(24, 0.30)
    sell[0] = 8.0  # spike in the current interval
    buy[0] = 8.3
    reserve = np.full(24, 6.0)
    inputs = make_inputs(buy=buy, sell=sell, soc0=8.0, reserve=reserve)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.05, reserve_penalty=0.5))
    assert sol.discharge_kw[0] == pytest.approx(BATTERY.max_discharge_kw, abs=0.01)
    assert sol.soc_kwh[1] < 6.0  # broke the reserve to sell into the real spike


def test_no_grid_charge_option():
    battery = BatteryParams(**{**BATTERY.__dict__, "allow_grid_charge": False})
    buy = np.full(24, 0.30)
    buy[0:6] = 0.01  # tempting grid charge
    inputs = make_inputs(buy=buy, sell=0.10, pv=0.0, soc0=2.0)
    sol = solve(inputs, battery, GRID, config(terminal_value=0.25))
    assert sol.charge_kw.max() < 0.01  # no PV -> no charging allowed


def test_soc_sensor_glitch_clamped_not_infeasible():
    inputs = make_inputs(soc0=15.0)  # above capacity
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.2))
    assert sol.ok
    assert sol.soc_kwh[0] == pytest.approx(BATTERY.soc_max_kwh)


def spike_prices() -> tuple[np.ndarray, np.ndarray]:
    buy = np.full(24, 0.30)
    sell = np.full(24, 0.10)
    buy[10:12], sell[10:12] = 5.3, 5.0
    return buy, sell


def test_no_simultaneous_charge_discharge():
    buy, sell = spike_prices()
    inputs = make_inputs(buy=buy, sell=sell, soc0=2.0)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.25))
    assert (np.minimum(sol.charge_kw, sol.discharge_kw) < 0.01).all()


def test_energy_balance_holds():
    buy, sell = spike_prices()
    inputs = make_inputs(buy=buy, sell=sell, pv=2.0, load=1.0, soc0=5.0)
    sol = solve(inputs, BATTERY, GRID, config(terminal_value=0.25))
    lhs = sol.pv_used_kw + sol.discharge_kw + sol.grid_import_kw
    rhs = inputs.load + sol.charge_kw + sol.grid_export_kw
    assert np.allclose(lhs, rhs, atol=1e-4)

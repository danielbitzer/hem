"""Backtest policies: the HEM MILP vs the baselines it must beat."""

from __future__ import annotations

from dataclasses import dataclass

from hem.backtest.sim import CycleRecord
from hem.config import Spike
from hem.models import Action
from hem.optimizer.model import (
    BatteryParams,
    GridParams,
    OptimizerConfig,
    OptimizerInputs,
    SolverError,
    auto_terminal_value,
    solve,
)
from hem.optimizer.result import classify_action
from hem.planner import discharge_cap_vector, spike_reserve_vector


@dataclass
class NoBatteryPolicy:
    name: str = "no-battery"

    def decide(self, record: CycleRecord, soc_kwh: float) -> float:
        return 0.0


@dataclass
class SelfConsumptionPolicy:
    """What the inverter does on its own: soak up PV surplus, cover load
    deficit, never trade with the grid."""

    name: str = "self-consumption"

    def decide(self, record: CycleRecord, soc_kwh: float) -> float:
        return float(record.pv[0] - record.load[0])  # BatterySim clamps


class HemPolicy:
    """Receding-horizon MPC: re-solve the MILP on each recorded forecast view,
    apply the step-0 decision. Runs the SAME strategy layers as the live
    planner — spike reserve, confirmed-spike discharge cap, and pin-and-compare
    hysteresis — via the shared functions in hem.planner, so the backtest gate
    measures the policy that would actually run."""

    name = "hem"

    def __init__(
        self,
        battery: BatteryParams,
        grid: GridParams,
        spike: Spike | None = None,
        action_switch_threshold_dollars: float = 0.02,
        solver_timeout_s: float = 30,
    ):
        self._battery = battery
        self._grid = grid
        self._spike = spike or Spike()
        self._switch_threshold = action_switch_threshold_dollars
        self._timeout = solver_timeout_s
        self._last_power = 0.0
        self._last_action: Action | None = None

    def build_inputs(self, record: CycleRecord, soc_kwh: float) -> OptimizerInputs:
        """Mirror of Planner.gather's strategy layers, via the same shared
        functions — public so tests can assert parity structurally."""
        return OptimizerInputs(
            dt_hours=record.dt_hours,
            buy=record.buy,
            sell=record.sell,
            pv=record.pv,
            load=record.load,
            soc0_kwh=soc_kwh,
            reserve_kwh=spike_reserve_vector(
                record.sell,
                record.dt_hours,
                lookahead_hours=self._spike.lookahead_hours,
                high_price_threshold=self._spike.high_price_threshold,
                reserve_kwh=self._spike.reserve_kwh,
                soc_max_kwh=self._battery.soc_max_kwh,
            ),
            max_discharge_kw_step=discharge_cap_vector(
                len(record.dt_hours),
                record.live_spike,
                self._spike.discharge_kw,
                self._battery.max_discharge_kw,
            ),
        )

    def decide(self, record: CycleRecord, soc_kwh: float) -> float:
        inputs = self.build_inputs(record, soc_kwh)
        config = OptimizerConfig(
            terminal_value=auto_terminal_value(record.buy, self._battery),
            reserve_penalty_per_kwh=self._spike.reserve_penalty_per_kwh,
            solver_timeout_s=self._timeout,
        )
        try:
            solution = solve(inputs, self._battery, self._grid, config)
        except SolverError:
            return self._last_power  # hold the previous decision, like the live loop
        solution = self._hysteresis(solution, inputs, config)
        self._last_action = classify_action(
            float(solution.charge_kw[0]),
            float(solution.discharge_kw[0]),
            float(inputs.pv[0]),
            float(solution.pv_used_kw[0]),
            float(inputs.load[0]),
        )
        self._last_power = float(solution.charge_kw[0] - solution.discharge_kw[0])
        return self._last_power

    def _hysteresis(self, free, inputs: OptimizerInputs, config: OptimizerConfig):
        """Same pin-and-compare rule as Planner._apply_hysteresis."""
        if self._last_action is None or self._switch_threshold <= 0:
            return free
        free_action = classify_action(
            float(free.charge_kw[0]),
            float(free.discharge_kw[0]),
            float(inputs.pv[0]),
            float(free.pv_used_kw[0]),
            float(inputs.load[0]),
        )
        if free_action == self._last_action:
            return free
        try:
            pinned = solve(
                inputs, self._battery, self._grid, config, pin_step0=self._last_action.value
            )
        except SolverError:
            return free
        if pinned.objective - free.objective < self._switch_threshold:
            return pinned
        return free

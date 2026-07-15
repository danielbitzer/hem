"""Backtest policies: the HEM MILP vs the baselines it must beat."""

from __future__ import annotations

from dataclasses import dataclass

from hem.backtest.sim import CycleRecord
from hem.optimizer.model import (
    BatteryParams,
    GridParams,
    OptimizerConfig,
    OptimizerInputs,
    SolverError,
    auto_terminal_value,
    solve,
)


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
    apply the step-0 decision. Simulated SoC feeds back in — exactly the live
    loop, minus HA."""

    name = "hem"

    def __init__(
        self,
        battery: BatteryParams,
        grid: GridParams,
        reserve_penalty_per_kwh: float = 0.5,
        solver_timeout_s: float = 30,
    ):
        self._battery = battery
        self._grid = grid
        self._reserve_penalty = reserve_penalty_per_kwh
        self._timeout = solver_timeout_s
        self._last_power = 0.0

    def decide(self, record: CycleRecord, soc_kwh: float) -> float:
        inputs = OptimizerInputs(
            dt_hours=record.dt_hours,
            buy=record.buy,
            sell=record.sell,
            pv=record.pv,
            load=record.load,
            soc0_kwh=soc_kwh,
        )
        config = OptimizerConfig(
            terminal_value=auto_terminal_value(record.buy, self._battery),
            reserve_penalty_per_kwh=self._reserve_penalty,
            solver_timeout_s=self._timeout,
        )
        try:
            solution = solve(inputs, self._battery, self._grid, config)
        except SolverError:
            return self._last_power  # hold the previous decision, like the live loop
        self._last_power = float(solution.charge_kw[0] - solution.discharge_kw[0])
        return self._last_power

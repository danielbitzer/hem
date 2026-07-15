"""Map a raw MILP solution onto the Plan domain model."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from hem.models import Action, Plan, PlanInterval
from hem.optimizer.model import OptimizerInputs, Solution
from hem.timegrid import TimeGrid

POWER_TOL_KW = 0.01
CURTAIL_TOL_KW = 0.05


def classify_action(
    charge_kw: float, discharge_kw: float, pv_kw: float, pv_used_kw: float
) -> Action:
    if discharge_kw > POWER_TOL_KW:
        return Action.DISCHARGE
    if charge_kw > POWER_TOL_KW:
        return Action.CHARGE
    if pv_kw > CURTAIL_TOL_KW and pv_used_kw < pv_kw - CURTAIL_TOL_KW:
        return Action.CURTAIL
    return Action.IDLE


def solution_to_plan(
    solution: Solution,
    grid: TimeGrid,
    inputs: OptimizerInputs,
    computed_at: datetime | None = None,
) -> Plan:
    intervals: list[PlanInterval] = []
    net_battery = solution.charge_kw - solution.discharge_kw
    dt = inputs.dt_hours
    interval_cost = (
        inputs.buy * solution.grid_import_kw - inputs.sell * solution.grid_export_kw
    ) * dt
    for i, step in enumerate(grid.steps):
        intervals.append(
            PlanInterval(
                start=step.start,
                end=step.end,
                action=classify_action(
                    solution.charge_kw[i],
                    solution.discharge_kw[i],
                    inputs.pv[i],
                    solution.pv_used_kw[i],
                ),
                power_kw=float(net_battery[i]),
                soc_start=float(solution.soc_kwh[i]),
                soc_end=float(solution.soc_kwh[i + 1]),
                buy=float(inputs.buy[i]),
                sell=float(inputs.sell[i]),
                pv_kw=float(inputs.pv[i]),
                load_kw=float(inputs.load[i]),
                grid_import_kw=float(solution.grid_import_kw[i]),
                grid_export_kw=float(solution.grid_export_kw[i]),
                interval_cost=float(interval_cost[i]),
            )
        )
    return Plan(
        intervals=intervals,
        objective_cost=float(np.sum(interval_cost)),
        solver_status=solution.status,
        solve_ms=solution.solve_ms,
        computed_at=computed_at or datetime.now(UTC),
    )

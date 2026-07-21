"""The MILP: battery charge/discharge scheduling over the rolling horizon.

Formulation (see IMPLEMENTATION_PLAN.md §3):

    min  Σ (buy·gi − sell·ge)·Δt            energy cost/revenue
       + wear · Σ pd·Δt                      battery wear on discharge
       + ε · Σ (pc + pd)·Δt                  anti-chatter tiebreak
       + reserve_penalty · Σ slack·Δt        soft spike-reserve violations
       + target_penalty · Σ tslack           soft daily-SoC-target shortfall
       − v_T · soc[T]                        terminal SoC value

    s.t. pv_u + pd + gi == load + pc + ge    power balance per step
         0 ≤ pv_u ≤ pv                       curtailment allowed
         soc[t+1] == soc[t] + (ηc·pc − pd/ηd)·Δt
         soc bounds; pc ≤ Pc·y; pd ≤ Pd·(1−y)   no simultaneous charge+discharge
         gi ≤ Gi; ge ≤ Ge
         soc[t] ≥ reserve[t] − slack[t]      soft floor (spike readiness)
         soc[k] ≥ target[k] − tslack[k]      soft instants (daily full-charge)
         optional: pc ≤ pv_u                 (allow_grid_charge=false)

Grid shape varies per cycle (data-driven), so the problem is rebuilt each
solve — HiGHS handles ~80 steps in tens of ms, so parameter caching isn't
worth the fixed-shape constraint it would impose.

Note: scalar boolean cvxpy Variables crash on solution unpacking with HiGHS
(cvxpy 1.6.x bug) — y is always a vector here, which is fine.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import cvxpy as cp
import numpy as np

log = logging.getLogger(__name__)

EPSILON_CHATTER = 0.0005  # $/kWh tiebreak against pointless cycling
SELL_BUY_MARGIN = 0.001  # enforced sell < buy gap, $/kWh


@dataclass(frozen=True)
class BatteryParams:
    capacity_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    efficiency_charge: float
    efficiency_discharge: float
    soc_min_kwh: float
    soc_max_kwh: float
    wear_cost_per_kwh: float
    allow_grid_charge: bool


@dataclass(frozen=True)
class GridParams:
    import_limit_kw: float
    export_limit_kw: float
    # Below this feed-in price ($/kWh), forbid BATTERY-sourced grid export —
    # the battery still covers the house, but never sells stored energy this
    # cheap. PV surplus can still export. None = no floor.
    min_export_price: float | None = None


@dataclass(frozen=True)
class OptimizerInputs:
    dt_hours: np.ndarray  # step widths, hours
    buy: np.ndarray  # $/kWh per step
    sell: np.ndarray
    pv: np.ndarray  # kW per step
    load: np.ndarray
    soc0_kwh: float
    reserve_kwh: np.ndarray | None = None  # soft SoC floor per step (spike readiness)
    # Per-step discharge cap override (kW); None -> battery.max_discharge_kw
    # everywhere. Used to raise the cap during a confirmed spike interval.
    max_discharge_kw_step: np.ndarray | None = None
    # Soft INSTANTANEOUS SoC targets, length T+1 aligned with soc[]; 0 =
    # inactive. Used for the daily full-charge insurance target: unlike the
    # reserve (a floor over a window), this binds at single instants — the
    # battery is free to discharge hard right after each target.
    soc_target_kwh: np.ndarray | None = None


@dataclass(frozen=True)
class OptimizerConfig:
    terminal_value: float  # $/kWh valuing residual stored energy
    # Slack cost accumulates per step: effectively $/kWh PER HOUR spent below
    # the reserve floor, so persistent violations cost more than momentary ones.
    reserve_penalty_per_kwh: float
    solver_timeout_s: float
    # $/kWh charged ONCE per missing kWh at each soc_target_kwh instant — the
    # explicit insurance premium for not being full at the daily target.
    soc_target_penalty_per_kwh: float = 0.0


@dataclass
class Solution:
    status: str
    objective: float
    solve_ms: float
    charge_kw: np.ndarray
    discharge_kw: np.ndarray
    grid_import_kw: np.ndarray
    grid_export_kw: np.ndarray
    pv_used_kw: np.ndarray
    soc_kwh: np.ndarray  # length T+1

    @property
    def ok(self) -> bool:
        return self.status in ("optimal", "optimal_inaccurate")


class SolverError(Exception):
    pass


def auto_terminal_value(buy: np.ndarray, battery: BatteryParams) -> float:
    """Value residual stored energy at 'what buying later would plausibly cost':
    median buy price discounted by discharge efficiency, net of wear. This is
    the correct NET value of a held kWh (you recover it later at ~median buy
    through the discharge efficiency, paying wear to do so), and keeping the
    wear term is what preserves self-consumption on flat/mild days — dropping
    it makes the battery hoard and import instead of running the house.

    NB a side effect (see grid.min_export_price): when the median buy is low and
    wear is high this floors near $0, so the battery will export 'excess' stored
    energy at any feed-in above ~wear. That's economically marginal but often
    unwanted — min_export_price is the hard floor to forbid it."""
    return max(
        0.0,
        float(np.median(buy)) * battery.efficiency_discharge - battery.wear_cost_per_kwh,
    )


def solve(
    inputs: OptimizerInputs,
    battery: BatteryParams,
    grid: GridParams,
    config: OptimizerConfig,
    pin_step0: str | None = None,
) -> Solution:
    """pin_step0 constrains the first step's battery mode ('charge' /
    'discharge' / 'idle') — used by the planner's hysteresis to price the
    previous action before allowing a switch.

    Actions are grid-coupled (see classify_action): 'idle' pins step 0 to the
    self-consumption envelope (charge from PV only, no battery export), NOT a
    frozen battery — the inverter's idle mode still serves load and soaks up
    PV surplus."""
    T = len(inputs.dt_hours)
    if not (len(inputs.buy) == len(inputs.sell) == len(inputs.pv) == len(inputs.load) == T):
        raise ValueError("all input arrays must have the same length")
    dt = inputs.dt_hours
    buy = inputs.buy
    # Keep sell strictly below buy so simultaneous import+export is never
    # optimal (true for Amber anyway; guards degenerate LP directions).
    sell = np.minimum(inputs.sell, buy - SELL_BUY_MARGIN)
    # Start from the ACTUAL SoC, even below soc_min — clamping it up to the
    # floor invents energy that isn't there (seen live: a BMS recalibration
    # dropped the real SoC below the planning reserve and the plan kept
    # spending the phantom 4+ kWh). The hard floor relaxes to the actual
    # start, so a below-reserve battery can never be discharged further and
    # recovers above soc_min when prices make charging worthwhile. Clip only
    # to the physical [0, soc_max] against sensor glitches.
    soc0 = float(np.clip(inputs.soc0_kwh, 0.0, battery.soc_max_kwh))
    soc_floor = min(battery.soc_min_kwh, soc0)

    pc = cp.Variable(T, nonneg=True)
    pd = cp.Variable(T, nonneg=True)
    gi = cp.Variable(T, nonneg=True)
    ge = cp.Variable(T, nonneg=True)
    pv_u = cp.Variable(T, nonneg=True)
    soc = cp.Variable(T + 1)
    y = cp.Variable(T, boolean=True)

    constraints = [
        pv_u + pd + gi == inputs.load + pc + ge,
        pv_u <= inputs.pv,
        soc[0] == soc0,
        soc[1:]
        == soc[:-1]
        + cp.multiply(battery.efficiency_charge * pc - pd / battery.efficiency_discharge, dt),
        soc >= soc_floor,
        soc <= battery.soc_max_kwh,
        pc <= battery.max_charge_kw * y,
        pd
        <= cp.multiply(
            inputs.max_discharge_kw_step
            if inputs.max_discharge_kw_step is not None
            else np.full(T, battery.max_discharge_kw),
            1 - y,
        ),
        gi <= grid.import_limit_kw,
        ge <= grid.export_limit_kw,
    ]
    if not battery.allow_grid_charge:
        constraints.append(pc <= pv_u)
    # Min-export-price guard: at steps whose feed-in price is below the floor,
    # cap grid export at the PV surplus (pv_u - pc), so the battery can still
    # cover load and import but never sources export — no selling stored energy
    # below the floor. Uses the raw forecast sell (not the buy-clamped copy).
    if grid.min_export_price is not None:
        below = np.where(inputs.sell < grid.min_export_price)[0]
        if below.size:
            constraints.append(ge[below] <= pv_u[below] - pc[below])
    # The self-consumption envelope: charge from PV only, export only PV
    # leftovers (no battery export); serving load from the battery is free.
    self_consumption = [pc[0] <= pv_u[0], ge[0] <= pv_u[0] - pc[0]]
    if pin_step0 == "charge":
        constraints += [pd[0] == 0, pc[0] >= 0.01]
    elif pin_step0 == "discharge":
        constraints += [pc[0] == 0, pd[0] >= 0.01]
    elif pin_step0 == "no_charge":
        constraints += [*self_consumption, pc[0] == 0]  # block charging
    elif pin_step0 in ("idle", "curtail"):
        constraints += self_consumption

    cost = (
        cp.sum(cp.multiply(buy, cp.multiply(gi, dt)))
        - cp.sum(cp.multiply(sell, cp.multiply(ge, dt)))
        + battery.wear_cost_per_kwh * cp.sum(cp.multiply(pd, dt))
        + EPSILON_CHATTER * cp.sum(cp.multiply(pc + pd, dt))
        - config.terminal_value * soc[T]
    )
    if (
        inputs.soc_target_kwh is not None
        and np.any(inputs.soc_target_kwh > 0)
        and config.soc_target_penalty_per_kwh > 0
    ):
        # One-shot penalty per instant (no dt weighting): being short at the
        # target moment is the insured event, however long the steps around it.
        target_slack = cp.Variable(T + 1, nonneg=True)
        constraints.append(soc >= inputs.soc_target_kwh - target_slack)
        cost = cost + config.soc_target_penalty_per_kwh * cp.sum(target_slack)
    if inputs.reserve_kwh is not None and np.any(inputs.reserve_kwh > 0):
        slack = cp.Variable(T, nonneg=True)
        constraints.append(soc[1:] >= inputs.reserve_kwh - slack)
        cost = cost + config.reserve_penalty_per_kwh * cp.sum(cp.multiply(slack, dt))

    problem = cp.Problem(cp.Minimize(cost), constraints)
    start = time.perf_counter()
    try:
        problem.solve(solver="HIGHS", time_limit=config.solver_timeout_s)
    except cp.error.SolverError as e:
        raise SolverError(str(e)) from e
    solve_ms = (time.perf_counter() - start) * 1000

    if problem.status not in ("optimal", "optimal_inaccurate") or soc.value is None:
        raise SolverError(f"solver returned status={problem.status}")

    return Solution(
        status=problem.status,
        objective=float(problem.value),
        solve_ms=solve_ms,
        charge_kw=np.asarray(pc.value).clip(min=0),
        discharge_kw=np.asarray(pd.value).clip(min=0),
        grid_import_kw=np.asarray(gi.value).clip(min=0),
        grid_export_kw=np.asarray(ge.value).clip(min=0),
        pv_used_kw=np.asarray(pv_u.value).clip(min=0),
        soc_kwh=np.asarray(soc.value),
    )

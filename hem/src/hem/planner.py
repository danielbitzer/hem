"""One optimization cycle: gather -> normalize -> solve -> Plan.

Also owns the judgment calls around the raw MILP:
- staleness policy (degraded inputs must never silently produce a plan)
- step-0 price override with the live 5-min prices
- forecast haircut (distant sell prices discounted toward the median)
- spike reserve trigger (soft SoC floor while a potential spike is ahead)
- hysteresis (pin-and-compare before switching the current action)
- live-spike guard (never grid-charge during a confirmed spike)
- fallback (reuse the previous plan when the solver fails)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from hem.adapters.amber import AmberExpressAdapter
from hem.adapters.solar import OpenMeteoSolarAdapter
from hem.adapters.sungrow import SungrowAdapter
from hem.adapters.weather import WeatherAdapter
from hem.config import Settings
from hem.forecast.load import BaselineLoadForecaster
from hem.models import Action, BatteryState, Plan, PriceForecast, Series
from hem.optimizer.model import (
    BatteryParams,
    GridParams,
    OptimizerConfig,
    OptimizerInputs,
    SolverError,
    auto_terminal_value,
    solve,
)
from hem.optimizer.result import classify_action, solution_to_plan
from hem.timegrid import TimeGrid, resample_mean, resample_previous

log = logging.getLogger(__name__)

MAX_PRICE_AGE = timedelta(minutes=15)
MAX_SOC_AGE = timedelta(minutes=10)
HAIRCUT_START = timedelta(hours=6)


class InputsStale(Exception):
    pass


@dataclass
class CycleData:
    grid: TimeGrid
    inputs: OptimizerInputs
    prices: PriceForecast
    battery: BatteryState
    temps: np.ndarray | None


class Planner:
    def __init__(
        self,
        settings: Settings,
        *,
        prices: AmberExpressAdapter,
        solar: OpenMeteoSolarAdapter,
        battery: SungrowAdapter,
        weather: WeatherAdapter,
        tz: ZoneInfo,
    ):
        self._settings = settings
        self._prices = prices
        self._solar = solar
        self._battery = battery
        self._weather = weather
        self._load_forecaster = BaselineLoadForecaster(settings.load_profile, tz)
        self._battery_params = _battery_params(settings)
        self._grid_params = GridParams(
            import_limit_kw=settings.grid.import_limit_kw,
            export_limit_kw=settings.grid.export_limit_kw,
        )
        self.previous_plan: Plan | None = None

    async def gather(self, now: datetime) -> CycleData:
        prices, pv, battery = await asyncio.gather(
            self._prices.get_prices(),
            self._solar.get_pv(),
            self._battery.get_battery_state(),
        )
        temps_series: Series | None
        try:
            temps_series = await self._weather.get_temperature_forecast()
        except Exception as e:  # noqa: BLE001 - temps are optional, never fatal
            log.warning("temperature forecast unavailable (%s); load rules disabled", e)
            temps_series = None

        if prices.updated_at and now - prices.updated_at > MAX_PRICE_AGE:
            raise InputsStale(f"prices last updated {prices.updated_at.isoformat()}")
        if now - battery.ts > MAX_SOC_AGE:
            raise InputsStale(f"battery state last updated {battery.ts.isoformat()}")

        horizon = timedelta(hours=self._settings.optimizer.horizon_hours)
        grid = TimeGrid.build(now, sorted({*prices.buy.times, *prices.sell.times}), horizon)

        buy = resample_previous(prices.buy, grid)
        sell_raw = resample_previous(prices.sell, grid)
        buy[0], sell_raw[0] = prices.current_buy, prices.current_sell
        # The haircut tempers the objective's trust in distant prices; the
        # spike reserve triggers on the RAW forecast — it exists precisely to
        # hedge prices the haircut would discount.
        sell = self._haircut_sell(sell_raw, grid, now)

        pv_kw = resample_mean(pv, grid)
        temps = resample_previous(temps_series, grid) if temps_series else None
        load_kw = self._load_forecaster.forecast(grid, temps)

        inputs = OptimizerInputs(
            dt_hours=grid.dt_hours,
            buy=buy,
            sell=sell,
            pv=pv_kw,
            load=load_kw,
            soc0_kwh=battery.soc_frac * self._battery_params.capacity_kwh,
            reserve_kwh=self._spike_reserve(sell_raw, grid, now, prices),
        )
        return CycleData(grid=grid, inputs=inputs, prices=prices, battery=battery, temps=temps)

    def _haircut_sell(self, sell: np.ndarray, grid: TimeGrid, now: datetime) -> np.ndarray:
        """Discount above-median sell prices beyond HAIRCUT_START toward the
        median: distant forecast spikes shouldn't distort near-term decisions."""
        h = self._settings.optimizer.forecast_haircut
        if h <= 0:
            return sell
        median = float(np.median(sell))
        out = sell.copy()
        for i, step in enumerate(grid.steps):
            if step.start - now >= HAIRCUT_START and out[i] > median:
                out[i] = median + (out[i] - median) * (1 - h)
        return out

    def _spike_reserve(
        self, sell: np.ndarray, grid: TimeGrid, now: datetime, prices: PriceForecast
    ) -> np.ndarray | None:
        """Soft SoC floor up to the first high-price step within the lookahead
        window, so energy is held ready to sell into a potential spike."""
        cfg = self._settings.spike
        if cfg.reserve_kwh <= 0:
            return None
        lookahead = timedelta(hours=cfg.lookahead_hours)
        trigger = None
        for i, step in enumerate(grid.steps):
            if step.start - now > lookahead:
                break
            if sell[i] >= cfg.high_price_threshold:
                trigger = i
                break
        if trigger is None or trigger == 0:
            return None  # no potential spike ahead, or it's already here — sell, don't hold
        reserve = np.zeros(len(grid))
        reserve[:trigger] = min(cfg.reserve_kwh, self._battery_params.soc_max_kwh)
        log.info(
            "spike reserve armed: %.1f kWh held until %s (sell %.2f $/kWh)",
            reserve[0],
            grid.steps[trigger].start.isoformat(),
            sell[trigger],
        )
        return reserve

    def optimize(self, data: CycleData, now: datetime) -> Plan:
        cfg = self._settings.optimizer
        terminal = (
            auto_terminal_value(data.inputs.buy, self._battery_params)
            if cfg.terminal_soc_value == "auto"
            else float(cfg.terminal_soc_value)
        )
        opt_config = OptimizerConfig(
            terminal_value=terminal,
            reserve_penalty_per_kwh=self._settings.spike.reserve_penalty_per_kwh,
            solver_timeout_s=cfg.solver_timeout_s,
        )
        solution = solve(data.inputs, self._battery_params, self._grid_params, opt_config)
        solution = self._apply_hysteresis(solution, data, opt_config)
        plan = solution_to_plan(solution, data.grid, data.inputs, computed_at=now)
        if solution.status.endswith("(hysteresis)"):
            plan.solver_status = solution.status
        plan = self._live_spike_guard(plan, data)
        return plan

    def _apply_hysteresis(self, free, data: CycleData, opt_config: OptimizerConfig):
        """Only switch away from the previous action if the free solution beats
        the action-pinned solution by more than the configured threshold —
        compared on the FULL solver objective (energy + wear + terminal value),
        not just the energy bill."""
        threshold = self._settings.optimizer.action_switch_threshold_dollars
        prev = self.previous_plan
        if prev is None or not prev.intervals or threshold <= 0:
            return free
        prev_action = prev.intervals[0].action
        free_action = classify_action(
            float(free.charge_kw[0]),
            float(free.discharge_kw[0]),
            float(data.inputs.pv[0]),
            float(free.pv_used_kw[0]),
        )
        if free_action == prev_action:
            return free
        try:
            pinned = solve(
                data.inputs,
                self._battery_params,
                self._grid_params,
                opt_config,
                pin_step0=prev_action.value,
            )
        except SolverError:
            return free  # previous action no longer feasible; switch
        gain = pinned.objective - free.objective
        if gain < threshold:
            log.debug("hysteresis: keeping %s (switch would gain only $%.4f)", prev_action, gain)
            pinned.status = f"{pinned.status} (hysteresis)"
            return pinned
        return free

    def _live_spike_guard(self, plan: Plan, data: CycleData) -> Plan:
        """Belt-and-braces: never grid-charge during a confirmed price spike."""
        if not data.prices.live_spike or not plan.intervals:
            return plan
        step0 = plan.intervals[0]
        if step0.action == Action.CHARGE and step0.grid_import_kw > 0.01:
            log.warning("live spike active: suppressing planned grid charge")
            step0.action = Action.IDLE
            step0.power_kw = 0.0
        return plan

    async def run_cycle(self, now: datetime | None = None) -> Plan:
        now = now or datetime.now(UTC)
        try:
            data = await self.gather(now)
            plan = self.optimize(data, now)
        except SolverError as e:
            log.error("solver failed: %s", e)
            plan = self._fallback(now)
        self.previous_plan = plan
        return plan

    def _fallback(self, now: datetime) -> Plan:
        """Shift the previous plan forward, dropping elapsed intervals."""
        prev = self.previous_plan
        if prev is None:
            raise SolverError("solver failed and no previous plan to fall back on")
        remaining = [iv for iv in prev.intervals if iv.end > now]
        if not remaining:
            raise SolverError("solver failed and previous plan is fully elapsed")
        return Plan(
            intervals=remaining,
            objective_cost=prev.objective_cost,
            solver_status="stale (reusing previous plan)",
            solve_ms=0.0,
            computed_at=prev.computed_at,
        )


def _battery_params(settings: Settings) -> BatteryParams:
    b = settings.battery
    return BatteryParams(
        capacity_kwh=b.capacity_kwh,
        max_charge_kw=b.max_charge_kw,
        max_discharge_kw=b.max_discharge_kw,
        efficiency_charge=b.efficiency_charge,
        efficiency_discharge=b.efficiency_discharge,
        soc_min_kwh=b.soc_min * b.capacity_kwh,
        soc_max_kwh=b.soc_max * b.capacity_kwh,
        wear_cost_per_kwh=b.wear_cost_per_kwh,
        allow_grid_charge=b.allow_grid_charge,
    )

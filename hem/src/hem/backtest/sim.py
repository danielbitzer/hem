"""Receding-horizon backtest simulator.

Replays recorded cycles (recorder.py "inputs" records): at each recorded
cycle a policy decides the battery power from the simulated battery state
plus that cycle's forecast view; the decision is applied until the next
record using that record's step-0 prices/PV/load as the actuals.

Known v1 limitation: step-0 forecast PV/load stand in for metered actuals.
Once grid/PV meter sensors are recorded too, swap them in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from hem.optimizer.model import BatteryParams


@dataclass
class CycleRecord:
    """One recorded planner cycle, parsed from recorder JSONL."""

    ts: datetime
    dt_hours: np.ndarray
    buy: np.ndarray
    sell: np.ndarray
    pv: np.ndarray
    load: np.ndarray
    current_buy: float
    current_sell: float
    live_spike: bool

    @classmethod
    def from_json(cls, record: dict[str, Any]) -> CycleRecord:
        data = record["data"]
        return cls(
            ts=datetime.fromisoformat(record["ts"]),
            dt_hours=np.asarray(data["dt_hours"], dtype=float),
            buy=np.asarray(data["buy"], dtype=float),
            sell=np.asarray(data["sell"], dtype=float),
            pv=np.asarray(data["pv_kw"], dtype=float),
            load=np.asarray(data["load_kw"], dtype=float),
            current_buy=float(data["prices"]["current_buy"]),
            current_sell=float(data["prices"]["current_sell"]),
            live_spike=bool(data["prices"].get("live_spike", False)),
        )


class BatterySim:
    """Battery physics: clamps requested power to limits and SoC bounds."""

    def __init__(self, params: BatteryParams, soc_kwh: float):
        self.params = params
        self.soc_kwh = float(np.clip(soc_kwh, params.soc_min_kwh, params.soc_max_kwh))
        self.charged_kwh = 0.0  # busbar energy in
        self.discharged_kwh = 0.0  # busbar energy out

    def apply(self, power_kw: float, dt_hours: float) -> float:
        """Apply a requested battery power (positive = charge) for dt_hours;
        returns the actually-achieved busbar power after clamping."""
        p = self.params
        if power_kw >= 0:
            kw = min(power_kw, p.max_charge_kw)
            headroom_kw = (p.soc_max_kwh - self.soc_kwh) / (p.efficiency_charge * dt_hours)
            kw = max(0.0, min(kw, headroom_kw))
            self.soc_kwh += p.efficiency_charge * kw * dt_hours
            self.charged_kwh += kw * dt_hours
            return kw
        kw = min(-power_kw, p.max_discharge_kw)
        available_kw = (self.soc_kwh - p.soc_min_kwh) * p.efficiency_discharge / dt_hours
        kw = max(0.0, min(kw, available_kw))
        self.soc_kwh -= kw / p.efficiency_discharge * dt_hours
        self.discharged_kwh += kw * dt_hours
        return -kw


class Policy(Protocol):
    name: str

    def decide(self, record: CycleRecord, soc_kwh: float) -> float:
        """Requested battery power in kW (positive = charge) for this cycle."""
        ...


@dataclass
class StepResult:
    ts: datetime
    battery_kw: float
    grid_import_kw: float
    grid_export_kw: float
    cost: float
    soc_kwh: float
    spike: bool


@dataclass
class SimResult:
    policy: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return sum(s.cost for s in self.steps)

    @property
    def days(self) -> float:
        if len(self.steps) < 2:
            return 0.0
        span = self.steps[-1].ts - self.steps[0].ts
        return span.total_seconds() / 86400

    @property
    def cost_per_day(self) -> float:
        return self.total_cost / self.days if self.days else 0.0

    def spike_revenue(self) -> float:
        return sum(-s.cost for s in self.steps if s.spike and s.cost < 0)


def simulate(
    policy: Policy,
    records: list[CycleRecord],
    battery: BatteryParams,
    export_limit_kw: float,
    soc0_kwh: float,
) -> SimResult:
    sim = BatterySim(battery, soc0_kwh)
    result = SimResult(policy=policy.name)
    for i, rec in enumerate(records):
        if i + 1 < len(records):
            dt = (records[i + 1].ts - rec.ts).total_seconds() / 3600
        else:
            dt = float(rec.dt_hours[0])
        dt = min(max(dt, 1 / 60), 1.0)  # guard against gaps in the recording
        battery_kw = sim.apply(policy.decide(rec, sim.soc_kwh), dt)
        pv, load = float(rec.pv[0]), float(rec.load[0])
        net_kw = load + battery_kw - pv  # positive -> import
        grid_import = max(0.0, net_kw)
        grid_export = min(max(0.0, -net_kw), export_limit_kw)  # excess beyond limit curtailed
        cost = (rec.current_buy * grid_import - rec.current_sell * grid_export) * dt
        result.steps.append(
            StepResult(
                ts=rec.ts,
                battery_kw=battery_kw,
                grid_import_kw=grid_import,
                grid_export_kw=grid_export,
                cost=cost,
                soc_kwh=sim.soc_kwh,
                spike=rec.live_spike,
            )
        )
    return result

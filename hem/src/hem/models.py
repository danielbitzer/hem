"""Shared domain types.

Internal conventions (adapters normalize to these, nothing else re-converts):
- prices in $/kWh; feed-in positive = revenue
- battery power positive = charging
- all timestamps tz-aware UTC; Series times are interval starts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class Series:
    """Timestamped series at a source-native resolution (pre-grid-resampling)."""

    times: list[datetime]
    values: list[float]
    duration: timedelta  # native interval length (30 min Amber, 15 min Open-Meteo)

    def __post_init__(self) -> None:
        if len(self.times) != len(self.values):
            raise ValueError(f"times ({len(self.times)}) != values ({len(self.values)})")


@dataclass
class PriceForecast:
    buy: Series
    sell: Series
    spike: list[bool]  # per buy interval: spike_status in {potential, spike}
    current_buy: float  # live price sensor values (5-min updates)
    current_sell: float
    # per buy interval, advanced-price band if the source provides it (amber_express)
    buy_high: list[float] = field(default_factory=list)
    sell_high: list[float] = field(default_factory=list)
    live_spike: bool = False


@dataclass
class BatteryState:
    soc_frac: float
    power_kw: float  # positive = charging
    capacity_kwh: float
    ts: datetime


class Action(StrEnum):
    CHARGE = "charge"
    DISCHARGE = "discharge"
    IDLE = "idle"
    CURTAIL = "curtail"


@dataclass
class PlanInterval:
    start: datetime
    end: datetime
    action: Action
    power_kw: float  # battery power, positive = charging
    soc_start: float
    soc_end: float
    buy: float
    sell: float
    pv_kw: float
    load_kw: float
    grid_import_kw: float
    grid_export_kw: float
    interval_cost: float


@dataclass
class Plan:
    intervals: list[PlanInterval]
    objective_cost: float
    solver_status: str
    solve_ms: float
    computed_at: datetime

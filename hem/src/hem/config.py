"""Configuration loading.

Two sources, resolved independently:

- HA connection: supervisor proxy when SUPERVISOR_TOKEN is present (add-on),
  otherwise HEM_HA_URL + HEM_HA_TOKEN (standalone/dev).
- Options: /data/options.json (Supervisor-rendered add-on options), overridable
  with HEM_OPTIONS_FILE for standalone/dev.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

DEFAULT_OPTIONS_FILE = "/data/options.json"


@dataclass(frozen=True)
class HaConnection:
    rest_url: str  # base REST API url, no trailing slash, includes /api
    ws_url: str
    token: str


def resolve_connection(env: dict[str, str] | None = None) -> HaConnection:
    env = env if env is not None else dict(os.environ)
    if token := env.get("SUPERVISOR_TOKEN"):
        return HaConnection(
            rest_url="http://supervisor/core/api",
            ws_url="ws://supervisor/core/websocket",
            token=token,
        )
    try:
        url = env["HEM_HA_URL"].rstrip("/")
        token = env["HEM_HA_TOKEN"]
    except KeyError as e:
        raise RuntimeError(
            "Not running under the Supervisor and standalone connection is not "
            f"configured: missing {e.args[0]}. Set HEM_HA_URL and HEM_HA_TOKEN."
        ) from None
    ws_scheme = "wss" if url.startswith("https") else "ws"
    host = url.split("://", 1)[1]
    return HaConnection(
        rest_url=f"{url}/api",
        ws_url=f"{ws_scheme}://{host}/api/websocket",
        token=token,
    )


class Entities(BaseModel):
    buy_price: str
    sell_price: str
    # For amber_express the forecast attributes live on the price sensors, so these
    # default to the price sensors. For amber_core point them at the Forecast sensors.
    buy_forecast: str = ""
    sell_forecast: str = ""
    price_spike: str = ""
    pv_forecast_today: str
    pv_forecast_tomorrow: str
    battery_soc: str
    battery_power: str
    weather: str

    @model_validator(mode="after")
    def _default_forecast_entities(self) -> Self:
        if not self.buy_forecast:
            self.buy_forecast = self.buy_price
        if not self.sell_forecast:
            self.sell_forecast = self.sell_price
        return self


class Battery(BaseModel):
    capacity_kwh: float = Field(gt=0)
    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)
    efficiency_charge: float = Field(default=0.95, gt=0.5, le=1)
    efficiency_discharge: float = Field(default=0.95, gt=0.5, le=1)
    soc_min: float = Field(default=0.10, ge=0, le=1)
    soc_max: float = Field(default=1.0, ge=0, le=1)
    wear_cost_per_kwh: float = Field(default=0.04, ge=0)
    allow_grid_charge: bool = True

    @model_validator(mode="after")
    def _soc_bounds_ordered(self) -> Self:
        if self.soc_min >= self.soc_max:
            raise ValueError("battery.soc_min must be < battery.soc_max")
        return self


class Grid(BaseModel):
    import_limit_kw: float = Field(gt=0)
    export_limit_kw: float = Field(ge=0)


class TempRule(BaseModel):
    when: Literal["temp_above", "temp_below"]
    threshold_c: float
    add_kw: float = Field(ge=0)


class LoadProfile(BaseModel):
    weekday_kw: list[float] = Field(min_length=24, max_length=24)
    weekend_kw: list[float] = Field(min_length=24, max_length=24)
    temp_rules: list[TempRule] = []


class Optimizer(BaseModel):
    horizon_hours: int = Field(default=36, ge=2, le=72)
    terminal_soc_value: Literal["auto"] | float = "auto"
    solver_timeout_s: int = Field(default=30, ge=1, le=300)
    action_switch_threshold_dollars: float = Field(default=0.02, ge=0)
    forecast_haircut: float = Field(default=0.2, ge=0, le=1)


class Spike(BaseModel):
    lookahead_hours: float = Field(default=4, ge=0)
    reserve_kwh: float = Field(default=6.0, ge=0)
    high_price_threshold: float = Field(default=1.0, ge=0)
    reserve_penalty_per_kwh: float = Field(default=0.5, ge=0)


class Control(BaseModel):
    mode: Literal["dry_run", "active"] = "dry_run"
    max_writes_per_hour: int = Field(default=12, ge=1, le=60)


class Settings(BaseModel):
    price_source: Literal["amber_express", "amber_core"] = "amber_express"
    entities: Entities
    battery: Battery
    grid: Grid
    load_profile: LoadProfile
    optimizer: Optimizer = Optimizer()
    spike: Spike = Spike()
    control: Control = Control()
    log_level: Literal["debug", "info", "warning", "error"] = "info"


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.environ.get("HEM_OPTIONS_FILE", DEFAULT_OPTIONS_FILE))
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raise RuntimeError(
            f"Options file not found: {path}. Under the Supervisor this is rendered "
            "automatically; standalone, point HEM_OPTIONS_FILE at your options JSON."
        ) from None
    return Settings.model_validate(raw)

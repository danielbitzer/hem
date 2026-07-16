"""Configuration loading.

Two sources, resolved independently:

- Environment (EnvSettings, pydantic-settings): HEM_* env vars and hem/.env.
  Under the Supervisor, SUPERVISOR_TOKEN wins and the proxy URLs are used;
  standalone needs HEM_HA_URL + HEM_HA_TOKEN.
- Options: /data/options.json (Supervisor-rendered add-on options), or
  HEM_OPTIONS_FILE standalone.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OPTIONS_FILE = "/data/options.json"


class EnvSettings(BaseSettings):
    """HEM_* environment variables, also read from ./.env in dev."""

    model_config = SettingsConfigDict(
        env_prefix="HEM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    ha_url: str = ""  # HEM_HA_URL, standalone only
    ha_token: str = ""  # HEM_HA_TOKEN, standalone only
    options_file: Path | None = None  # HEM_OPTIONS_FILE
    data_dir: Path | None = None  # HEM_DATA_DIR (history/recordings)
    log_level: str = ""  # HEM_LOG_LEVEL, overrides options log_level when set


@dataclass(frozen=True)
class HaConnection:
    rest_url: str  # base REST API url, no trailing slash, includes /api
    ws_url: str
    token: str


def _supervisor_token(explicit: str | None) -> str:
    return explicit if explicit is not None else os.environ.get("SUPERVISOR_TOKEN", "")


def resolve_connection(env: EnvSettings, supervisor_token: str | None = None) -> HaConnection:
    if token := _supervisor_token(supervisor_token):
        return HaConnection(
            rest_url="http://supervisor/core/api",
            ws_url="ws://supervisor/core/websocket",
            token=token,
        )
    if not env.ha_url or not env.ha_token:
        raise RuntimeError(
            "Not running under the Supervisor and standalone connection is not "
            "configured. Set HEM_HA_URL and HEM_HA_TOKEN (env or hem/.env)."
        )
    url = env.ha_url.rstrip("/")
    ws_scheme = "wss" if url.startswith("https") else "ws"
    host = url.split("://", 1)[1]
    return HaConnection(
        rest_url=f"{url}/api",
        ws_url=f"{ws_scheme}://{host}/api/websocket",
        token=env.ha_token,
    )


def resolve_data_dir(env: EnvSettings, supervisor_token: str | None = None) -> Path:
    """/data under the Supervisor (persistent add-on storage); ./data standalone."""
    if env.data_dir:
        return env.data_dir
    return Path("/data") if _supervisor_token(supervisor_token) else Path("data")


class Entities(BaseModel):
    buy_price: str
    sell_price: str
    # Amber Express forecast attributes live on the price sensors, so these
    # default to the price sensors; override only for exotic setups.
    buy_forecast: str = ""
    sell_forecast: str = ""
    price_spike: str = ""
    pv_forecast_today: str
    pv_forecast_tomorrow: str
    battery_soc: str
    battery_power: str
    weather: str
    # House load power sensor (W or kW) — the load forecast is learned from
    # its history (e.g. the mkaiser package's sensor.load_power). Optional but
    # strongly recommended: without it HEM plans with ZERO house load and
    # reports a degraded load forecast.
    load_power: str = ""
    # Outdoor temperature sensor with long-term statistics (state_class set).
    # Optional; enables the learned temperature response (load vs
    # cooling/heating degrees).
    outdoor_temp: str = ""

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
    # Sign of the battery power sensor. The mkaiser Sungrow package reports
    # positive while DISCHARGING (confirmed on Dan's install), so
    # charge_negative is the default; HEM's internal convention is positive =
    # charging. Set charge_positive if your sensor reads the other way.
    power_convention: Literal["charge_positive", "charge_negative"] = "charge_negative"

    @model_validator(mode="after")
    def _soc_bounds_ordered(self) -> Self:
        if self.soc_min >= self.soc_max:
            raise ValueError("battery.soc_min must be < battery.soc_max")
        return self


class Grid(BaseModel):
    import_limit_kw: float = Field(gt=0)
    export_limit_kw: float = Field(ge=0)


class LoadForecast(BaseModel):
    # Learning window. Hourly long-term statistics reach months back; the raw
    # recorder-history fallback is capped by the recorder purge window (~10
    # days) regardless of this value. With entities.outdoor_temp configured,
    # the effective window is additionally capped to the overlap between load
    # and temperature history.
    history_days: int = Field(default=60, ge=1, le=365)


class Optimizer(BaseModel):
    horizon_hours: int = Field(default=36, ge=2, le=72)
    terminal_soc_value: Literal["auto"] | float = "auto"
    # must stay below the 90s cycle timeout in main.py
    solver_timeout_s: int = Field(default=30, ge=1, le=60)
    action_switch_threshold_dollars: float = Field(default=0.02, ge=0)
    forecast_haircut: float = Field(default=0.2, ge=0, le=1)


class Spike(BaseModel):
    lookahead_hours: float = Field(default=4, ge=0)
    reserve_kwh: float = Field(default=6.0, ge=0)
    high_price_threshold: float = Field(default=1.0, ge=0)
    reserve_penalty_per_kwh: float = Field(default=0.5, ge=0)
    # Discharge cap while a CONFIRMED spike is active (current interval only).
    # Lets a wear-conscious everyday max_discharge_kw be exceeded for the rare
    # high-value hours. 0 = disabled (always use battery.max_discharge_kw).
    discharge_kw: float = Field(default=0.0, ge=0)


class Settings(BaseModel):
    entities: Entities
    battery: Battery
    grid: Grid
    load_forecast: LoadForecast = LoadForecast()
    optimizer: Optimizer = Optimizer()
    spike: Spike = Spike()
    log_level: Literal["debug", "info", "warning", "error"] = "info"


DEV_OPTIONS_FALLBACK = "dev-options.json"


def load_settings(path: str | Path | None = None) -> Settings:
    candidates = (
        [Path(path)] if path else [Path(DEFAULT_OPTIONS_FILE), Path(DEV_OPTIONS_FALLBACK)]
    )
    for candidate in candidates:
        if candidate.exists():
            return Settings.model_validate(json.loads(candidate.read_text()))
    raise RuntimeError(
        f"Options file not found (tried {', '.join(str(c) for c in candidates)}). "
        "Under the Supervisor /data/options.json is rendered automatically; "
        "standalone, create ./dev-options.json or set HEM_OPTIONS_FILE."
    )

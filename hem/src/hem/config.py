"""Configuration loading.

Three sources, resolved independently:

- Environment (EnvSettings, pydantic-settings): HEM_* env vars and hem/.env.
  Under the Supervisor, SUPERVISOR_TOKEN wins and the proxy URLs are used;
  standalone needs HEM_HA_URL + HEM_HA_TOKEN.
- Supervisor options (/data/options.json): log_level ONLY — everything else
  is configured in the web UI (issue #5).
- The HEM-owned config document (see config_store): the pydantic Settings
  model below, edited via the dashboard's Settings view and validated here —
  the exact model the planner consumes, so the UI can never accept a config
  the app would reject.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, tzinfo
from datetime import time as dt_time
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
    config_file: Path | None = None  # HEM_CONFIG_FILE, overrides the hem-config.json path
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


class Entities(BaseModel):
    # Amber Express price sensors; their `forecast` attribute carries the
    # price forecast, so no separate forecast entities exist.
    buy_price: str
    sell_price: str
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
    # Daily full-charge insurance: softly require SoC >= daily_target_soc at
    # daily_target_time (local) each day. The penalty is the premium you'll
    # pay per missing kWh — high enough to beat forgone feed-in on a normal
    # day, low enough that a genuinely better opportunity (a real spike)
    # still outbids it. 0 disables.
    daily_target_soc: float = Field(default=0.0, ge=0, le=1)
    daily_target_time: dt_time = dt_time(15, 0)
    daily_target_penalty_per_kwh: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _soc_bounds_ordered(self) -> Self:
        if self.soc_min >= self.soc_max:
            raise ValueError("battery.soc_min must be < battery.soc_max")
        if self.daily_target_time.tzinfo is not None:
            raise ValueError("battery.daily_target_time must be a plain local time (no offset)")
        return self


class Grid(BaseModel):
    import_limit_kw: float = Field(gt=0)
    export_limit_kw: float = Field(ge=0)


class Vacation(BaseModel):
    """Vacation mode: the household is away, so the learned load forecast is
    wrong — replace it with a flat standby baseline (fridge, network, pumps)
    and free the rest of the battery for the market. No temperature response
    (nobody is running the AC) and no load.buffer (the baseline is already a
    deliberate number) while active. `until` (local time, optional) auto-
    expires the mode; if it lands inside the horizon, steps after it revert
    to the learned forecast — the plan already covers your return evening."""

    enabled: bool = False
    baseline_kw: float = Field(default=0.3, ge=0)
    until: datetime | None = None

    def active(self, now: datetime, tz: tzinfo) -> bool:
        if not self.enabled:
            return False
        return self.until is None or now < self.until_utc(tz)

    def until_utc(self, tz: tzinfo) -> datetime | None:
        """`until` as an aware instant; a naive value (what the UI's local
        datetime picker submits) is interpreted in HEM's local timezone."""
        if self.until is None:
            return None
        return self.until.replace(tzinfo=tz) if self.until.tzinfo is None else self.until


class Load(BaseModel):
    # Safety margin on the learned load forecast: the whole forecast vector is
    # scaled by (1 + buffer), after the temperature response. The learned
    # profile is a mean — this plans for consistently more than it. Distinct
    # from soc_min / the daily target, which shape SoC *policy*; the buffer
    # shapes the forecast itself.
    buffer: float = Field(default=0.0, ge=0, le=1)


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
    # Master switch, toggled in the web UI. While disabled (including the
    # not-yet-configured first boot) HEM publishes sensor.hem_status as
    # "disabled"/"unconfigured" instead of "ok", which trips the actuator
    # blueprint's failsafe: the inverter reverts to self-consumption.
    enabled: bool = False
    entities: Entities
    battery: Battery
    grid: Grid
    load: Load = Load()
    vacation: Vacation = Vacation()
    optimizer: Optimizer = Optimizer()
    spike: Spike = Spike()


def resolve_log_level(env: EnvSettings) -> str:
    """HEM_LOG_LEVEL, else log_level from the Supervisor-rendered add-on
    options — the only option left in config.yaml — else info."""
    if env.log_level:
        return env.log_level
    try:
        level = json.loads(Path(DEFAULT_OPTIONS_FILE).read_text()).get("log_level")
    except (OSError, ValueError):
        return "info"
    return level or "info"

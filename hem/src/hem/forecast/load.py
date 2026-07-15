"""Household load forecasting.

v1 is rule-based: a per-hour-of-day baseline (weekday/weekend) plus additive
temperature rules (heating/cooling kW when the forecast temperature crosses a
threshold). The LoadForecaster protocol is the seam for a future learned
forecaster (e.g. from HA recorder history).

The grid is UTC but people live in local time, so the hour-of-day lookup uses
the configured local timezone (half-hour offsets like Adelaide's +09:30 mean a
30-min grid step maps cleanly onto a single local hour... almost: a step
straddling a local hour boundary uses its midpoint's hour).
"""

from __future__ import annotations

import os
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from hem.config import LoadProfile
from hem.timegrid import TimeGrid


class LoadForecaster(Protocol):
    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        """Expected household load in kW per grid step."""
        ...


class BaselineLoadForecaster:
    def __init__(self, profile: LoadProfile, tz: ZoneInfo):
        self._profile = profile
        self._tz = tz

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        out = np.empty(len(grid))
        for i, step in enumerate(grid.steps):
            mid = (step.start + (step.end - step.start) / 2).astimezone(self._tz)
            hourly = (
                self._profile.weekend_kw if mid.weekday() >= 5 else self._profile.weekday_kw
            )
            out[i] = hourly[mid.hour]
        if temps_c is not None:
            if len(temps_c) != len(grid):
                raise ValueError(f"temps ({len(temps_c)}) != grid steps ({len(grid)})")
            for rule in self._profile.temp_rules:
                if rule.when == "temp_above":
                    out = out + np.where(temps_c > rule.threshold_c, rule.add_kw, 0.0)
                else:
                    out = out + np.where(temps_c < rule.threshold_c, rule.add_kw, 0.0)
        return out


def default_timezone() -> ZoneInfo:
    """Local timezone from the TZ env var (set for HA add-ons); UTC otherwise.

    A UTC fallback shifts the load profile rather than crashing; the profile
    hours are user-relative so a wrong zone shows up as an offset baseline.
    """
    try:
        return ZoneInfo(os.environ["TZ"])
    except (KeyError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")

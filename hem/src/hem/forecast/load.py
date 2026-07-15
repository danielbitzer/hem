"""Household load forecasting.

Two forecasters behind one protocol:

- BaselineLoadForecaster (load_profile.source: profile): a per-hour-of-day
  baseline (weekday/weekend) from config.
- HistoryLoadForecaster (load_profile.source: history): learns the hourly
  baseline from the household's actual consumption — HA recorder history of a
  configured load-power sensor over the last N days, time-weighted per local
  hour-of-day × weekday/weekend. Hours with too little data fall back to the
  configured profile, as does everything when history is unavailable, so the
  configured profile remains the safety net, not dead config.

Both apply additive temperature rules (heating/cooling kW when the forecast
temperature crosses a threshold) on top. NOTE for history mode: the learned
averages already include typical seasonal heating/cooling, so keep temp_rules
for extreme-day corrections only (or empty) to avoid double counting.

The grid is UTC but people live in local time, so the hour-of-day lookup uses
the configured local timezone (half-hour offsets like Adelaide's +09:30 mean a
30-min grid step maps cleanly onto a single local hour... almost: a step
straddling a local hour boundary uses its midpoint's hour).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from hem.config import LoadProfile
from hem.ha.client import HaClient
from hem.timegrid import TimeGrid

log = logging.getLogger(__name__)

# A history sample holds until the next one; cap how long a single sample can
# count for so recorder gaps (HA restarts, purged data) don't let one stale
# value dominate its bucket.
MAX_SEGMENT = timedelta(minutes=30)
# Minimum observed hours in a (daytype, hour) bucket before trusting it over
# the configured profile — 2h over a 14-day window means the bucket is real.
MIN_BUCKET_HOURS = 2.0
REFRESH_INTERVAL = timedelta(hours=6)
RETRY_INTERVAL = timedelta(minutes=30)

_UNIT_TO_KW = {"W": 0.001, "kW": 1.0, "w": 0.001, "kw": 1.0}


class LoadForecaster(Protocol):
    async def refresh(self, now: datetime) -> None:
        """Update any learned state (rate-limited internally; never raises)."""
        ...

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        """Expected household load in kW per grid step."""
        ...


def _apply_temp_rules(
    out: np.ndarray, profile: LoadProfile, grid: TimeGrid, temps_c: np.ndarray | None
) -> np.ndarray:
    if temps_c is None:
        return out
    if len(temps_c) != len(grid):
        raise ValueError(f"temps ({len(temps_c)}) != grid steps ({len(grid)})")
    for rule in profile.temp_rules:
        if rule.when == "temp_above":
            out = out + np.where(temps_c > rule.threshold_c, rule.add_kw, 0.0)
        else:
            out = out + np.where(temps_c < rule.threshold_c, rule.add_kw, 0.0)
    return out


def _step_bucket(step_start: datetime, step_end: datetime, tz: ZoneInfo) -> tuple[int, int]:
    """(is_weekend, local hour) for a grid step, keyed by its midpoint."""
    mid = (step_start + (step_end - step_start) / 2).astimezone(tz)
    return (1 if mid.weekday() >= 5 else 0, mid.hour)


class BaselineLoadForecaster:
    def __init__(self, profile: LoadProfile, tz: ZoneInfo):
        self._profile = profile
        self._tz = tz

    async def refresh(self, now: datetime) -> None:
        return None

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        out = np.empty(len(grid))
        for i, step in enumerate(grid.steps):
            weekend, hour = _step_bucket(step.start, step.end, self._tz)
            hourly = self._profile.weekend_kw if weekend else self._profile.weekday_kw
            out[i] = hourly[hour]
        return _apply_temp_rules(out, self._profile, grid, temps_c)


def learn_hourly_profile(
    samples: list[tuple[datetime, float]],
    tz: ZoneInfo,
    *,
    min_bucket_hours: float = MIN_BUCKET_HOURS,
    max_segment: timedelta = MAX_SEGMENT,
) -> list[list[float | None]]:
    """Time-weighted mean kW per (daytype, local hour) from a piecewise-constant
    sample series. Returns [weekday, weekend] 24-value tables; None where the
    bucket has less than min_bucket_hours of observed time.

    Pure so it's testable without HA; samples must be time-ascending kW values.
    """
    energy = np.zeros((2, 24))  # kWh accumulated per bucket
    hours = np.zeros((2, 24))
    for (t0, v0), (t1, _) in zip(samples, samples[1:], strict=False):
        dur = min(t1 - t0, max_segment)
        if dur.total_seconds() <= 0:
            continue
        weekend, hour = _step_bucket(t0, t0 + dur, tz)
        dt_h = dur.total_seconds() / 3600
        energy[weekend][hour] += max(v0, 0.0) * dt_h
        hours[weekend][hour] += dt_h
    return [
        [
            float(energy[d][h] / hours[d][h]) if hours[d][h] >= min_bucket_hours else None
            for h in range(24)
        ]
        for d in (0, 1)
    ]


class HistoryLoadForecaster:
    """Learns the hourly baseline from HA recorder history of a load sensor.

    refresh() re-learns every REFRESH_INTERVAL and is deliberately never fatal:
    on any failure the last learned tables (or the configured profile) keep
    serving and a retry happens after RETRY_INTERVAL.
    """

    def __init__(
        self,
        client: HaClient,
        entity_id: str,
        profile: LoadProfile,
        tz: ZoneInfo,
        *,
        history_days: int = 14,
    ):
        self._client = client
        self._entity_id = entity_id
        self._profile = profile
        self._tz = tz
        self._days = history_days
        self._learned: list[list[float | None]] | None = None
        self._next_refresh: datetime | None = None

    async def refresh(self, now: datetime) -> None:
        if self._next_refresh and now < self._next_refresh:
            return
        try:
            self._learned = await self._learn(now)
            self._next_refresh = now + REFRESH_INTERVAL
            known = sum(v is not None for table in self._learned for v in table)
            log.info(
                "load history learned from %s: %d/48 hour buckets covered "
                "(rest use the configured profile)",
                self._entity_id,
                known,
            )
        except Exception as e:  # noqa: BLE001 - learned load is best-effort by design
            self._next_refresh = now + RETRY_INTERVAL
            log.warning(
                "could not learn load history from %s (%s); using %s",
                self._entity_id,
                e,
                "previous learned profile" if self._learned else "configured profile",
            )

    async def _learn(self, now: datetime) -> list[list[float | None]]:
        state = await self._client.get_state(self._entity_id)
        unit = state.attributes.get("unit_of_measurement")
        if unit not in _UNIT_TO_KW:
            raise ValueError(f"{self._entity_id} unit {unit!r} is not W/kW")
        scale = _UNIT_TO_KW[unit]
        raw = await self._client.get_history(
            self._entity_id, now - timedelta(days=self._days), now
        )
        samples: list[tuple[datetime, float]] = []
        for ts, value in raw:
            try:
                samples.append((ts, float(value) * scale))
            except ValueError:
                continue  # unavailable/unknown gaps
        if len(samples) < 2:
            raise ValueError(f"not enough history samples ({len(samples)})")
        return learn_hourly_profile(samples, self._tz)

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        out = np.empty(len(grid))
        for i, step in enumerate(grid.steps):
            weekend, hour = _step_bucket(step.start, step.end, self._tz)
            learned = self._learned[weekend][hour] if self._learned else None
            if learned is None:
                hourly = self._profile.weekend_kw if weekend else self._profile.weekday_kw
                learned = hourly[hour]
            out[i] = learned
        return _apply_temp_rules(out, self._profile, grid, temps_c)


def build_load_forecaster(
    client: HaClient, settings_load_power: str, profile: LoadProfile, tz: ZoneInfo
) -> LoadForecaster:
    if profile.source == "history":
        return HistoryLoadForecaster(
            client, settings_load_power, profile, tz, history_days=profile.history_days
        )
    return BaselineLoadForecaster(profile, tz)


def default_timezone() -> ZoneInfo:
    """Local timezone from the TZ env var (set for HA add-ons); UTC otherwise.

    A UTC fallback shifts the load profile rather than crashing; the profile
    hours are user-relative so a wrong zone shows up as an offset baseline.
    """
    try:
        return ZoneInfo(os.environ["TZ"])
    except (KeyError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")

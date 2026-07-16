"""Household load forecasting — learned from actual consumption, or nothing.

There is deliberately no hand-typed hourly profile: HistoryLoadForecaster
learns hour-of-day × weekday/weekend averages from the household's real
consumption (entities.load_power), refreshed daily. Preferred data source is
hourly long-term statistics (which survive recorder purging, so the window
can be months), falling back to raw recorder history (~10 days) for sensors
without a state_class.

With an outdoor temperature sensor configured (entities.outdoor_temp), the
daily learn also fits a temperature response: pooled cooling/heating slopes
(kW per degree above/below balance temperatures) regressed against each hour
bucket's deviation from its own average. forecast() then applies the FORECAST
temperatures to those slopes — so the prediction tracks a heatwave arriving
after a mild fortnight instead of the trailing average lagging it. The model
is only ever fitted on hours where load AND temperature overlap, so a young
temperature sensor caps the effective window rather than skewing the fit.

Until learning succeeds (no load sensor configured, or no usable history yet)
HEM plans with ZERO house load and reports the degraded state via `status`,
which the dashboard and hem_status surface as a warning. The documented
mitigation is raising battery.soc_min until learning is active.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np

from hem.ha.client import HaClient
from hem.timegrid import TimeGrid

log = logging.getLogger(__name__)

# A raw-history sample holds until the next one; cap how long a single sample
# can count for so recorder gaps (HA restarts, purged data) don't let one
# stale value dominate its bucket.
MAX_SEGMENT = timedelta(minutes=30)
# Minimum observed hours in a (daytype, hour) bucket before trusting it over
# the mean of the buckets that do have data.
MIN_BUCKET_HOURS = 2.0
# Minimum hours carrying both load and temperature before fitting slopes —
# 3 days of joint data; below that the regression is noise.
MIN_TEMP_HOURS = 72
# Minimum hourly statistics rows to prefer LTS over raw recorder history.
MIN_STATS_HOURS = 24
REFRESH_INTERVAL = timedelta(hours=24)
RETRY_INTERVAL = timedelta(minutes=30)
# Unit plausibility: no house has a median hourly load above this, so a
# median beyond it means the sensor's declared unit lies (e.g. the mkaiser
# package's load_power declares kW while emitting watt-magnitude values —
# seen live on Dan's install, where it produced a 35 kW/°C heating slope and
# an infeasible MILP).
MAX_PLAUSIBLE_MEDIAN_KW = 50.0
# Physics plausibility for the fitted temperature response: whole-house
# heating/cooling beyond this per degree means the fit chased an artifact —
# drop the response rather than forecast nonsense.
MAX_SLOPE_KW_PER_DEG = 2.0

_UNIT_TO_KW = {"W": 0.001, "kW": 1.0, "w": 0.001, "kw": 1.0}

# learned: a model is active. pending: a load sensor is configured but no
# model has been learned yet. unconfigured: no load sensor at all. Anything
# but "learned" means HEM is planning with zero house load.
LoadForecastStatus = Literal["learned", "pending", "unconfigured"]


class LoadForecaster(Protocol):
    @property
    def status(self) -> LoadForecastStatus: ...

    async def refresh(self, now: datetime) -> None:
        """Update any learned state (rate-limited internally; never raises)."""
        ...

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        """Expected household load in kW per grid step."""
        ...


class UnconfiguredLoadForecaster:
    """No load sensor: plan with zero load and say so loudly."""

    status: LoadForecastStatus = "unconfigured"

    async def refresh(self, now: datetime) -> None:
        return None

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        return np.zeros(len(grid))


def _step_bucket(step_start: datetime, step_end: datetime, tz: ZoneInfo) -> tuple[int, int]:
    """(is_weekend, local hour) for a grid step, keyed by its midpoint."""
    mid = (step_start + (step_end - step_start) / 2).astimezone(tz)
    return (1 if mid.weekday() >= 5 else 0, mid.hour)


@dataclass
class LoadModel:
    """Learned hourly baseline plus optional pooled temperature response.

    Prediction: base[bucket] + cool_slope * (cdh(T) - bucket's mean cdh)
                             + heat_slope * (hdh(T) - bucket's mean hdh)
    i.e. unbiased at the bucket's historically-typical temperature, shifted by
    how far the forecast deviates from it.
    """

    base: list[list[float | None]]  # (daytype, hour) mean kW; None = thin bucket
    cdh_mean: np.ndarray  # (2, 24) mean cooling degrees per bucket
    hdh_mean: np.ndarray
    cool_kw_per_deg: float = 0.0
    heat_kw_per_deg: float = 0.0
    balance_cool_c: float = 22.0
    balance_heat_c: float = 15.0
    has_temp_response: bool = False
    # highest hourly load ever observed — predictions never extrapolate past
    # it, no matter what the forecast temperature says
    max_kw: float = float("inf")

    @property
    def known_buckets(self) -> int:
        return sum(v is not None for table in self.base for v in table)

    @property
    def mean_kw(self) -> float:
        """Mean of the known buckets — the fallback for thin ones."""
        values = [v for table in self.base for v in table if v is not None]
        return float(np.mean(values)) if values else 0.0

    def predict(self, weekend: int, hour: int, temp_c: float | None) -> float:
        value = self.base[weekend][hour]
        if value is None:
            return self.mean_kw
        if self.has_temp_response and temp_c is not None:
            cdh = max(temp_c - self.balance_cool_c, 0.0)
            hdh = max(self.balance_heat_c - temp_c, 0.0)
            value += self.cool_kw_per_deg * (cdh - self.cdh_mean[weekend][hour])
            value += self.heat_kw_per_deg * (hdh - self.hdh_mean[weekend][hour])
        return min(max(value, 0.0), self.max_kw)


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


def _fit_slopes(dc: np.ndarray, dh: np.ndarray, dy: np.ndarray) -> tuple[float, float]:
    """Non-negative least squares for two regressors, by hand (it's 2x2).

    Degenerate columns (no temperature variation in that direction over the
    window — e.g. no cooling degrees in winter) get a zero slope.
    """

    def single(x: np.ndarray) -> float:
        d = float(x @ x)
        return max(float(x @ dy) / d, 0.0) if d > 1e-9 else 0.0

    has_c, has_h = float(dc @ dc) > 1e-9, float(dh @ dh) > 1e-9
    if has_c and has_h:
        a = np.array([[dc @ dc, dc @ dh], [dc @ dh, dh @ dh]])
        b = np.array([dc @ dy, dh @ dy])
        if abs(np.linalg.det(a)) > 1e-9:
            c, h = (float(v) for v in np.linalg.solve(a, b))
            if c < 0:
                return 0.0, single(dh)
            if h < 0:
                return single(dc), 0.0
            return c, h
        return single(dc), 0.0  # collinear: attribute to cooling arbitrarily
    return (single(dc), 0.0) if has_c else (0.0, single(dh))


def fit_load_model(
    records: list[tuple[datetime, float, float | None]],
    tz: ZoneInfo,
    *,
    balance_cool_c: float = 22.0,
    balance_heat_c: float = 15.0,
    min_bucket_hours: float = MIN_BUCKET_HOURS,
    min_temp_hours: int = MIN_TEMP_HOURS,
) -> LoadModel:
    """Fit a LoadModel from hourly (start, load_kw, temp_c | None) records.

    With enough joint load+temperature hours, ONLY those hours are used — this
    is the window cap: the bucket means and the regression see exactly the
    same data, and load history reaching further back than the temperature
    sensor's is excluded rather than mixed in. Without enough joint hours a
    base-only model is fitted on all records. Pure and synchronous for
    testability.
    """
    with_temp = [r for r in records if r[2] is not None]
    use_temp = len(with_temp) >= min_temp_hours
    rows = with_temp if use_temp else records
    if use_temp and len(with_temp) < len(records):
        joint_days = (with_temp[-1][0] - with_temp[0][0]).total_seconds() / 86400
        log.info(
            "learning window capped to load/temperature overlap: %d of %d "
            "hourly rows (~%.0f days)",
            len(with_temp),
            len(records),
            joint_days,
        )

    max_kw = max((max(r[1], 0.0) for r in rows), default=0.0)
    count = np.zeros((2, 24))
    load_sum = np.zeros((2, 24))
    cdh_sum = np.zeros((2, 24))
    hdh_sum = np.zeros((2, 24))
    for start, load_kw, temp_c in rows:
        weekend, hour = _step_bucket(start, start + timedelta(hours=1), tz)
        count[weekend][hour] += 1
        load_sum[weekend][hour] += max(load_kw, 0.0)
        if temp_c is not None:
            cdh_sum[weekend][hour] += max(temp_c - balance_cool_c, 0.0)
            hdh_sum[weekend][hour] += max(balance_heat_c - temp_c, 0.0)

    safe = np.maximum(count, 1)
    base_arr = load_sum / safe
    model = LoadModel(
        base=[
            [
                float(base_arr[d][h]) if count[d][h] >= min_bucket_hours else None
                for h in range(24)
            ]
            for d in (0, 1)
        ],
        cdh_mean=cdh_sum / safe,
        hdh_mean=hdh_sum / safe,
        balance_cool_c=balance_cool_c,
        balance_heat_c=balance_heat_c,
        max_kw=max_kw,
    )
    if not use_temp:
        return model

    dc, dh, dy = [], [], []
    for start, load_kw, temp_c in rows:
        weekend, hour = _step_bucket(start, start + timedelta(hours=1), tz)
        if count[weekend][hour] < min_bucket_hours:
            continue
        dc.append(max(temp_c - balance_cool_c, 0.0) - model.cdh_mean[weekend][hour])
        dh.append(max(balance_heat_c - temp_c, 0.0) - model.hdh_mean[weekend][hour])
        dy.append(max(load_kw, 0.0) - base_arr[weekend][hour])
    if dy:
        cool, heat = _fit_slopes(np.array(dc), np.array(dh), np.array(dy))
        if max(cool, heat) > MAX_SLOPE_KW_PER_DEG:
            log.warning(
                "fitted temperature response is implausible (%.2f kW/°C cooling, "
                "%.2f kW/°C heating > %.1f max); dropping it — check the load "
                "sensor's units and data",
                cool,
                heat,
                MAX_SLOPE_KW_PER_DEG,
            )
        else:
            model.cool_kw_per_deg, model.heat_kw_per_deg = cool, heat
            # counts as a response even if the house turned out temperature-
            # insensitive (slopes 0): the data spoke, the answer was "flat"
            model.has_temp_response = True
    return model


def _unit_correction(loads_kw: list[float], entity_id: str, source: str) -> float:
    """Extra scale factor when the declared unit is implausible.

    Trust the data over the label: a median hourly load beyond any real house
    means watt-magnitude values declared as kW (statistics metadata keeps the
    unit from when the sensor first recorded, so it can lie after a unit
    change). Without this, a mislabeled sensor inflates the load forecast
    1000x and makes the MILP infeasible.
    """
    median = float(np.median(loads_kw)) if loads_kw else 0.0
    if median > MAX_PLAUSIBLE_MEDIAN_KW:
        log.warning(
            "%s %s claims kW but the median value is %.0f — no house draws "
            "that; treating the values as watts",
            entity_id,
            source,
            median,
        )
        return 0.001
    return 1.0


def _to_celsius(value: float, unit: str | None) -> float | None:
    if unit in ("°C", "C"):
        return value
    if unit in ("°F", "F"):
        return (value - 32.0) * 5.0 / 9.0
    return None


class HistoryLoadForecaster:
    """Learns household load from history, refreshed daily.

    Data source preference per refresh: hourly long-term statistics over
    load_forecast.history_days (months-capable), then raw recorder history
    (~recorder purge window). Refresh is deliberately never fatal: on any
    failure the last learned model keeps serving (or zero load while status
    is "pending") and a retry happens after RETRY_INTERVAL.
    """

    def __init__(
        self,
        client: HaClient,
        entity_id: str,
        tz: ZoneInfo,
        *,
        temp_entity_id: str = "",
        history_days: int = 60,
    ):
        self._client = client
        self._entity_id = entity_id
        self._temp_entity_id = temp_entity_id
        self._tz = tz
        self._days = history_days
        self._model: LoadModel | None = None
        self._next_refresh: datetime | None = None

    @property
    def status(self) -> LoadForecastStatus:
        return "learned" if self._model is not None else "pending"

    async def refresh(self, now: datetime) -> None:
        if self._next_refresh and now < self._next_refresh:
            return
        try:
            self._model = await self._learn(now)
            self._next_refresh = now + REFRESH_INTERVAL
            if self._model.has_temp_response:
                log.info(
                    "load model learned from %s: %d/48 hour buckets, temperature "
                    "response %.3f kW/°C cooling / %.3f kW/°C heating",
                    self._entity_id,
                    self._model.known_buckets,
                    self._model.cool_kw_per_deg,
                    self._model.heat_kw_per_deg,
                )
            else:
                log.info(
                    "load model learned from %s: %d/48 hour buckets covered, no "
                    "temperature response",
                    self._entity_id,
                    self._model.known_buckets,
                )
        except Exception as e:  # noqa: BLE001 - learned load is best-effort by design
            self._next_refresh = now + RETRY_INTERVAL
            if self._model is None:
                log.warning(
                    "could not learn load history from %s (%s); planning with "
                    "ZERO house load until learning succeeds — consider raising "
                    "battery.soc_min meanwhile",
                    self._entity_id,
                    e,
                )
            else:
                log.warning(
                    "could not refresh load history from %s (%s); keeping the "
                    "previous learned model",
                    self._entity_id,
                    e,
                )

    async def _learn(self, now: datetime) -> LoadModel:
        try:
            model = await self._learn_statistics(now)
        except Exception as e:  # noqa: BLE001 - LTS is an upgrade, not a requirement
            log.info(
                "long-term statistics unavailable for %s (%s); falling back to "
                "recorder history",
                self._entity_id,
                e,
            )
            model = await self._learn_raw_history(now)
        if model.known_buckets == 0:
            raise ValueError("no hour bucket reached the minimum observed hours")
        return model

    async def _learn_statistics(self, now: datetime) -> LoadModel:
        """Learn from hourly LTS; the only path that can fit a temp response."""
        ids = [self._entity_id] + ([self._temp_entity_id] if self._temp_entity_id else [])
        units = await self._client.get_statistics_metadata(ids)
        if self._entity_id not in units:
            raise ValueError("no long-term statistics (sensor needs a state_class)")
        load_unit = units[self._entity_id]
        if load_unit not in _UNIT_TO_KW:
            raise ValueError(f"statistics unit {load_unit!r} is not W/kW")
        scale = _UNIT_TO_KW[load_unit]

        stats = await self._client.get_statistics(ids, now - timedelta(days=self._days), now)
        load_rows = stats.get(self._entity_id, [])
        if len(load_rows) < MIN_STATS_HOURS:
            raise ValueError(f"only {len(load_rows)} hourly statistics rows")

        temps: dict[datetime, float] = {}
        if self._temp_entity_id:
            if self._temp_entity_id in units:
                temp_unit = units[self._temp_entity_id]
                for ts, v in stats.get(self._temp_entity_id, []):
                    c = _to_celsius(v, temp_unit)
                    if c is not None:
                        temps[ts] = c
            if not temps:
                log.warning(
                    "outdoor_temp %s has no usable statistics; learning load "
                    "without a temperature response",
                    self._temp_entity_id,
                )
        loads = [v * scale for _, v in load_rows]
        scale *= _unit_correction(loads, self._entity_id, "statistics")
        records = [(ts, v * scale, temps.get(ts)) for ts, v in load_rows]
        return fit_load_model(records, self._tz)

    async def _learn_raw_history(self, now: datetime) -> LoadModel:
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
        correction = _unit_correction([v for _, v in samples], self._entity_id, "history")
        if correction != 1.0:
            samples = [(ts, v * correction) for ts, v in samples]
        base = learn_hourly_profile(samples, self._tz)
        return LoadModel(
            base=base,
            cdh_mean=np.zeros((2, 24)),
            hdh_mean=np.zeros((2, 24)),
            max_kw=max((max(v, 0.0) for _, v in samples), default=0.0),
        )

    def forecast(self, grid: TimeGrid, temps_c: np.ndarray | None) -> np.ndarray:
        if temps_c is not None and len(temps_c) != len(grid):
            raise ValueError(f"temps ({len(temps_c)}) != grid steps ({len(grid)})")
        model = self._model
        if model is None:
            return np.zeros(len(grid))
        out = np.empty(len(grid))
        for i, step in enumerate(grid.steps):
            weekend, hour = _step_bucket(step.start, step.end, self._tz)
            temp = float(temps_c[i]) if temps_c is not None else None
            out[i] = model.predict(weekend, hour, temp)
        return out


def build_load_forecaster(
    client: HaClient,
    load_power: str,
    tz: ZoneInfo,
    *,
    outdoor_temp: str = "",
    history_days: int = 60,
) -> LoadForecaster:
    if not load_power:
        log.warning(
            "entities.load_power is not configured: load forecasting is "
            "unavailable and HEM plans with ZERO house load. Configure a house "
            "load sensor; consider raising battery.soc_min meanwhile."
        )
        return UnconfiguredLoadForecaster()
    return HistoryLoadForecaster(
        client,
        load_power,
        tz,
        temp_entity_id=outdoor_temp,
        history_days=history_days,
    )


def default_timezone() -> ZoneInfo:
    """Local timezone from the TZ env var (set for HA add-ons); UTC otherwise.

    A UTC fallback shifts the learned hour-of-day buckets rather than
    crashing; a wrong zone shows up as an offset daily shape.
    """
    try:
        return ZoneInfo(os.environ["TZ"])
    except (KeyError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from conftest import FakeHa, fake_ha_client

from hem.config import LoadProfile
from hem.forecast.load import (
    BaselineLoadForecaster,
    HistoryLoadForecaster,
    fit_load_model,
    learn_hourly_profile,
)
from hem.timegrid import TimeGrid

ADELAIDE = ZoneInfo("Australia/Adelaide")

PROFILE = LoadProfile(
    weekday_kw=[float(h) / 10 for h in range(24)],  # hour h -> h/10 kW (recognizable)
    weekend_kw=[2.0] * 24,
)


def half_hour_grid(start_utc: datetime, hours: int) -> TimeGrid:
    bounds = [start_utc + timedelta(minutes=30 * i) for i in range(1, hours * 2)]
    return TimeGrid.build(start_utc, bounds, timedelta(hours=hours))


def test_local_hour_lookup_with_half_hour_offset():
    # 2026-07-15 is a Wednesday. 00:00 UTC == 09:30 in Adelaide (+09:30).
    grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 2)
    fc = BaselineLoadForecaster(PROFILE, ADELAIDE)
    out = fc.forecast(grid, None)
    # Steps: 09:30-10:00 (hour 9), 10:00-10:30, 10:30-11:00 (hour 10), 11:00-11:30 (11)
    assert out[0] == pytest.approx(0.9)
    assert out[1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(1.0)
    assert out[3] == pytest.approx(1.1)


def test_weekend_profile_selected_by_local_day():
    # Friday 14:30 UTC == Saturday 00:00 in Adelaide: local day decides.
    grid = half_hour_grid(datetime(2026, 7, 17, 14, 30, tzinfo=UTC), 1)
    out = BaselineLoadForecaster(PROFILE, ADELAIDE).forecast(grid, None)
    assert np.allclose(out, 2.0)


def test_temps_ignored_by_baseline():
    # profile mode has no temperature sensitivity — that's history+outdoor_temp's job
    grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 2)
    fc = BaselineLoadForecaster(PROFILE, ADELAIDE)
    assert np.allclose(fc.forecast(grid, np.array([30.0, 20.0, 8.0, 8.0])), fc.forecast(grid, None))


def test_temp_length_mismatch_rejected():
    grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 1)
    fc = HistoryLoadForecaster(None, "sensor.load_power", PROFILE, ADELAIDE)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="grid steps"):
        fc.forecast(grid, np.array([20.0]))


# --- history-learned profile ------------------------------------------------

# 2026-07-13/14/15 are Mon/Tue/Wed; Adelaide winter is UTC+9:30, so local
# hour 10 on those days is 00:30–01:30 UTC.


def local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=ADELAIDE).astimezone(UTC)


def hour_of_samples(day: int, hour: int, kw: float) -> list[tuple[datetime, float]]:
    """Samples every 15 min covering exactly local [hour:00, hour+1:00)."""
    return [(local(day, hour, m), kw) for m in (0, 15, 30, 45)] + [(local(day, hour + 1), kw)]


def test_learn_time_weighted_mean():
    # 15 min at 4 kW then 30 min at 0.8 kW -> time-weighted, not sample mean
    samples = [
        (local(15, 10, 0), 4.0),
        (local(15, 10, 15), 0.8),
        (local(15, 10, 45), 0.0),
    ]
    learned = learn_hourly_profile(samples, ADELAIDE, min_bucket_hours=0.5)
    assert learned[0][10] == pytest.approx((4.0 * 0.25 + 0.8 * 0.5) / 0.75)
    assert learned[0][11] is None  # trailing sample has no successor


def test_learn_gap_capped_and_thin_buckets_none():
    # one sample then a 6h gap: only MAX_SEGMENT (30 min) counts
    samples = [(local(15, 10, 0), 2.0), (local(15, 16, 0), 2.0)]
    learned = learn_hourly_profile(samples, ADELAIDE, min_bucket_hours=0.5)
    assert learned[0][10] == pytest.approx(2.0)
    learned = learn_hourly_profile(samples, ADELAIDE, min_bucket_hours=1.0)
    assert learned[0][10] is None  # 0.5h observed < 1h required


def test_learn_weekday_weekend_split_and_negative_clamp():
    # 18th is a Saturday; negative readings clamp to zero
    samples = hour_of_samples(15, 10, 1.5) + hour_of_samples(18, 10, -0.5)
    learned = learn_hourly_profile(samples, ADELAIDE, min_bucket_hours=0.5)
    assert learned[0][10] == pytest.approx(1.5)
    assert learned[1][10] == pytest.approx(0.0)


def history_items(samples: list[tuple[datetime, float]]) -> list[dict]:
    return [{"last_changed": ts.isoformat(), "state": str(v)} for ts, v in samples]


def load_power_state(unit: str | None) -> dict:
    attrs = {"unit_of_measurement": unit} if unit else {}
    return {
        "entity_id": "sensor.load_power",
        "state": "1500",
        "attributes": attrs,
        "last_updated": "2026-07-15T01:55:00+00:00",
    }


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


async def test_history_forecaster_learns_and_falls_back_per_hour():
    fake = FakeHa()
    fake.states["sensor.load_power"] = load_power_state("W")
    # 3 weekdays × 1h of samples at local hour 10 -> 3h >= MIN_BUCKET_HOURS;
    # values in W (1500 -> 1.5 kW)
    samples = [s for day in (13, 14, 15) for s in hour_of_samples(day, 10, 1500.0)]
    fake.history["sensor.load_power"] = history_items(samples)
    async with fake_ha_client(fake) as client:
        fc = HistoryLoadForecaster(client, "sensor.load_power", PROFILE, ADELAIDE)
        await fc.refresh(NOW)
        # local hours 9,10,10,11: only hour 10 is learned, rest from PROFILE
        grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 2)
        out = fc.forecast(grid, None)
    assert out[0] == pytest.approx(0.9)  # profile fallback (hour 9)
    assert out[1] == pytest.approx(1.5)  # learned (hour 10, W scaled to kW)
    assert out[2] == pytest.approx(1.5)
    assert out[3] == pytest.approx(1.1)  # profile fallback (hour 11)


async def test_history_refresh_rate_limited():
    fake = FakeHa()
    fake.states["sensor.load_power"] = load_power_state("W")
    fake.history["sensor.load_power"] = history_items(hour_of_samples(15, 10, 1000.0))
    async with fake_ha_client(fake) as client:
        fc = HistoryLoadForecaster(client, "sensor.load_power", PROFILE, ADELAIDE)
        await fc.refresh(NOW)
        await fc.refresh(NOW + timedelta(hours=7))
        assert len(fake.history_requests) == 1  # daily cadence: 7h is too soon
        await fc.refresh(NOW + timedelta(hours=25))
        assert len(fake.history_requests) == 2


async def test_history_failure_never_fatal_and_retries_later():
    fake = FakeHa()
    fake.states["sensor.load_power"] = load_power_state(None)  # unit missing
    async with fake_ha_client(fake) as client:
        fc = HistoryLoadForecaster(client, "sensor.load_power", PROFILE, ADELAIDE)
        await fc.refresh(NOW)  # must not raise
        grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 1)
        out = fc.forecast(grid, None)
        baseline = BaselineLoadForecaster(PROFILE, ADELAIDE).forecast(grid, None)
        assert np.allclose(out, baseline)
        # empty history (recorder purged) is also non-fatal
        fake.states["sensor.load_power"] = load_power_state("kW")
        await fc.refresh(NOW + timedelta(minutes=5))  # rate-limited, no call yet
        await fc.refresh(NOW + timedelta(minutes=31))
        assert len(fake.history_requests) == 1  # retried after RETRY_INTERVAL
        assert np.allclose(fc.forecast(grid, None), baseline)


# --- temperature response (long-term statistics) ------------------------------

# Synthetic truth: load = 0.4 kW + 0.1 kW per cooling degree above 22°C.
# Days alternate mild (18°C, cdh 0) and hot (28°C, cdh 6); Jul 13–17 are
# weekdays (18,28,18,28,18 -> bucket mean cdh 2.4, mean load 0.64 kW).

BASE_KW = 0.4
COOL_SLOPE = 0.1


def synth_day_temp(day: int) -> float:
    return 18.0 if (day - 13) % 2 == 0 else 28.0


def synth_records(days: range) -> list[tuple[datetime, float, float | None]]:
    out = []
    for day in days:
        t = synth_day_temp(day)
        kw = BASE_KW + COOL_SLOPE * max(t - 22.0, 0.0)
        out.extend((local(day, h), kw, t) for h in range(24))
    return out


def test_fit_recovers_cooling_slope_and_predicts_forecast_temps():
    model = fit_load_model(synth_records(range(13, 20)), ADELAIDE)
    assert model.has_temp_response
    assert model.cool_kw_per_deg == pytest.approx(COOL_SLOPE)
    assert model.heat_kw_per_deg == pytest.approx(0.0)
    # weekday bucket: unbiased at bucket-typical temp, tracks forecast deviation
    assert model.base[0][10] == pytest.approx(0.64)
    assert model.predict(0, 10, 30.0) == pytest.approx(BASE_KW + COOL_SLOPE * 8)
    assert model.predict(0, 10, 20.0) == pytest.approx(BASE_KW)
    assert model.predict(0, 10, None) == pytest.approx(0.64)  # no temps -> bucket mean


def test_fit_without_enough_temp_hours_is_base_only():
    records = [(ts, kw, None) for ts, kw, _ in synth_records(range(13, 20))]
    records[0] = (records[0][0], records[0][1], 25.0)  # one joint hour is not enough
    model = fit_load_model(records, ADELAIDE)
    assert not model.has_temp_response
    assert model.cool_kw_per_deg == 0.0


def stat_rows(values: list[tuple[datetime, float]]) -> list[dict]:
    return [{"start": int(ts.timestamp() * 1000), "mean": v} for ts, v in values]


async def test_lts_learning_with_temperature_response():
    fake = FakeHa()
    fake.statistics_meta = {"sensor.load_power": "W", "sensor.outdoor_temp": "°C"}
    records = synth_records(range(13, 20))
    fake.statistics = {
        "sensor.load_power": stat_rows([(ts, kw * 1000) for ts, kw, _ in records]),
        "sensor.outdoor_temp": stat_rows([(ts, t) for ts, _, t in records]),
    }
    async with fake_ha_client(fake) as client:
        fc = HistoryLoadForecaster(
            client,
            "sensor.load_power",
            PROFILE,
            ADELAIDE,
            temp_entity_id="sensor.outdoor_temp",
        )
        await fc.refresh(datetime(2026, 7, 20, 0, 0, tzinfo=UTC))
        assert len(fake.history_requests) == 0  # LTS path, no raw history needed
        grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 1)
        hot = fc.forecast(grid, np.array([30.0, 30.0]))
        mild = fc.forecast(grid, np.array([20.0, 20.0]))
    # forecast temps drive the learned response
    assert np.allclose(hot, BASE_KW + COOL_SLOPE * 8)
    assert np.allclose(mild, BASE_KW)


async def test_lts_unavailable_falls_back_to_raw_history():
    fake = FakeHa()  # no statistics_meta: sensor has no state_class
    fake.states["sensor.load_power"] = load_power_state("W")
    samples = [s for day in (13, 14, 15) for s in hour_of_samples(day, 10, 1500.0)]
    fake.history["sensor.load_power"] = history_items(samples)
    async with fake_ha_client(fake) as client:
        fc = HistoryLoadForecaster(client, "sensor.load_power", PROFILE, ADELAIDE)
        await fc.refresh(NOW)
        assert len(fake.history_requests) == 1
        grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 1)
        out = fc.forecast(grid, None)
    assert out[1] == pytest.approx(1.5)  # learned from raw history

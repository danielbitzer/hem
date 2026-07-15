from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from hem.config import LoadProfile, TempRule
from hem.forecast.load import BaselineLoadForecaster
from hem.timegrid import TimeGrid

ADELAIDE = ZoneInfo("Australia/Adelaide")

PROFILE = LoadProfile(
    weekday_kw=[float(h) / 10 for h in range(24)],  # hour h -> h/10 kW (recognizable)
    weekend_kw=[2.0] * 24,
    temp_rules=[
        TempRule(when="temp_above", threshold_c=28.0, add_kw=1.5),
        TempRule(when="temp_below", threshold_c=12.0, add_kw=1.2),
    ],
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


def test_temp_rules_additive():
    grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 2)
    fc = BaselineLoadForecaster(PROFILE, ADELAIDE)
    temps = np.array([30.0, 20.0, 8.0, 8.0])  # hot, mild, cold, cold
    out = fc.forecast(grid, temps)
    base = fc.forecast(grid, None)
    assert out[0] == pytest.approx(base[0] + 1.5)  # cooling
    assert out[1] == pytest.approx(base[1])  # no rule
    assert out[2] == pytest.approx(base[2] + 1.2)  # heating
    assert out[3] == pytest.approx(base[3] + 1.2)


def test_temp_length_mismatch_rejected():
    grid = half_hour_grid(datetime(2026, 7, 15, 0, 0, tzinfo=UTC), 1)
    with pytest.raises(ValueError, match="grid steps"):
        BaselineLoadForecaster(PROFILE, ADELAIDE).forecast(grid, np.array([20.0]))

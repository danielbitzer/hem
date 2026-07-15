from datetime import UTC, datetime, timedelta

from freezegun import freeze_time

from hem.main import seconds_to_next_boundary
from hem.web.app import HealthState


def test_boundary_mid_interval():
    # 09:02:30 → 150s to 09:05:00
    epoch = datetime(2026, 7, 15, 9, 2, 30, tzinfo=UTC).timestamp()
    assert seconds_to_next_boundary(epoch) == 150.0


def test_boundary_exactly_on_tick_waits_full_period():
    epoch = datetime(2026, 7, 15, 9, 5, 0, tzinfo=UTC).timestamp()
    assert seconds_to_next_boundary(epoch) == 300.0


def test_boundary_never_returns_less_than_1s():
    epoch = datetime(2026, 7, 15, 9, 4, 59, 500000, tzinfo=UTC).timestamp()
    assert seconds_to_next_boundary(epoch) >= 1.0


def test_health_grace_period_then_degraded():
    with freeze_time("2026-07-15 09:00:00", tz_offset=0) as frozen:
        health = HealthState()
        assert health.healthy  # startup grace
        frozen.tick(timedelta(minutes=16))
        assert not health.healthy  # no cycle ever succeeded
        health.mark_success()
        assert health.healthy
        frozen.tick(timedelta(minutes=14))
        assert health.healthy
        frozen.tick(timedelta(minutes=2))
        assert not health.healthy

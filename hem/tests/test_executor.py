from datetime import UTC, datetime, timedelta

from conftest import FakeHa, fake_ha_client

from hem.config import Settings
from hem.executor import DryRunExecutor, SungrowExecutor, WriteRateLimiter
from hem.models import Action, Plan, PlanInterval

NOW = datetime(2026, 7, 15, 11, 36, 30, tzinfo=UTC)

SETTINGS = Settings.model_validate(
    {
        "entities": {
            "buy_price": "sensor.b",
            "sell_price": "sensor.s",
            "pv_forecast_today": "sensor.p1",
            "pv_forecast_tomorrow": "sensor.p2",
            "battery_soc": "sensor.soc",
            "battery_power": "sensor.pw",
            "weather": "weather.w",
        },
        "battery": {"capacity_kwh": 12.8, "max_charge_kw": 5.0, "max_discharge_kw": 5.0},
        "grid": {"import_limit_kw": 15.0, "export_limit_kw": 5.0},
        "load_profile": {"weekday_kw": [0.5] * 24, "weekend_kw": [0.5] * 24},
        "control": {"mode": "active", "max_writes_per_hour": 12},
    }
)


def plan_with(action: Action, power_kw: float) -> Plan:
    iv = PlanInterval(
        start=NOW,
        end=NOW + timedelta(minutes=30),
        action=action,
        power_kw=power_kw,
        soc_start=6.4,
        soc_end=6.4,
        buy=0.44,
        sell=0.16,
        pv_kw=0.0,
        load_kw=1.0,
        grid_import_kw=0.0,
        grid_export_kw=0.0,
        interval_cost=0.0,
    )
    return Plan(
        intervals=[iv], objective_cost=0.0, solver_status="optimal", solve_ms=1.0, computed_at=NOW
    )


def add_override(fake: FakeHa, state: str) -> None:
    fake.states["input_boolean.hem_override"] = {
        "entity_id": "input_boolean.hem_override",
        "state": state,
        "attributes": {},
        "last_updated": NOW.isoformat(),
    }


async def test_dry_run_never_calls_services():
    fake = FakeHa()
    async with fake_ha_client(fake):
        await DryRunExecutor().apply(plan_with(Action.DISCHARGE, -5.0))
    assert fake.service_calls == []


async def test_discharge_writes_forced_mode_and_power():
    fake = FakeHa()
    add_override(fake, "off")
    async with fake_ha_client(fake) as client:
        ex = SungrowExecutor(client, SETTINGS)
        await ex.apply(plan_with(Action.DISCHARGE, -3.2))
    calls = fake.service_calls
    assert ("select", "select_option") == calls[0][:2]
    assert calls[0][2]["option"] == "Forced mode"
    assert calls[1][2]["option"] == "Forced discharge"
    assert calls[2][:2] == ("number", "set_value")
    assert calls[2][2]["value"] == 3200  # kW -> W, absolute


async def test_idle_reverts_to_self_consumption():
    fake = FakeHa()
    add_override(fake, "off")
    async with fake_ha_client(fake) as client:
        ex = SungrowExecutor(client, SETTINGS)
        await ex.apply(plan_with(Action.IDLE, 0.0))
    options = [c[2]["option"] for c in fake.service_calls]
    assert options == ["Stop (default)", "Self-consumption mode (default)"]


async def test_write_on_change_only():
    fake = FakeHa()
    add_override(fake, "off")
    async with fake_ha_client(fake) as client:
        ex = SungrowExecutor(client, SETTINGS)
        await ex.apply(plan_with(Action.CHARGE, 4.0))
        n = len(fake.service_calls)
        await ex.apply(plan_with(Action.CHARGE, 4.0))  # identical -> no new writes
    assert len(fake.service_calls) == n


async def test_override_halts_writes():
    fake = FakeHa()
    add_override(fake, "on")
    async with fake_ha_client(fake) as client:
        ex = SungrowExecutor(client, SETTINGS)
        await ex.apply(plan_with(Action.DISCHARGE, -5.0))
    assert fake.service_calls == []


async def test_power_clamped_to_limits():
    fake = FakeHa()
    add_override(fake, "off")
    async with fake_ha_client(fake) as client:
        ex = SungrowExecutor(client, SETTINGS)
        await ex.apply(plan_with(Action.CHARGE, 9.9))
    number_call = next(c for c in fake.service_calls if c[0] == "number")
    assert number_call[2]["value"] == 5000  # clamped to max_charge_kw


async def test_shutdown_reverts_mode():
    fake = FakeHa()
    async with fake_ha_client(fake) as client:
        await SungrowExecutor(client, SETTINGS).shutdown()
    options = [c[2]["option"] for c in fake.service_calls]
    assert "Self-consumption mode (default)" in options


def test_rate_limiter_window():
    limiter = WriteRateLimiter(max_per_hour=2)
    t0 = NOW
    assert limiter.allow(t0)
    assert limiter.allow(t0 + timedelta(minutes=1))
    assert not limiter.allow(t0 + timedelta(minutes=2))  # limit hit
    assert limiter.allow(t0 + timedelta(minutes=62))  # window rolled

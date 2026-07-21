"""Test mode: synthetic-scenario simulation + the /api/simulate endpoint."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from test_config import make_settings

from hem.config_store import ConfigController, ConfigStore
from hem.simulate import SCENARIOS, SimOverrides, run_simulation, scenario_list
from hem.web.app import AppState, create_app

ADELAIDE = ZoneInfo("Australia/Adelaide")
NOW = datetime(2026, 7, 21, 7, 30, tzinfo=UTC)  # ~5pm Adelaide


def _controller(tmp_path: Path, settings=None) -> ConfigController:
    store = ConfigStore(tmp_path / "hem-config.json")
    if settings is not None:
        store.save(settings)
    return ConfigController(store, settings)


def test_scenario_list_is_nonempty_and_well_formed():
    items = scenario_list()
    assert items and all({"id", "label", "description"} <= set(s) for s in items)
    assert set(SCENARIOS) == {s["id"] for s in items}


def test_run_simulation_returns_a_full_optimal_plan():
    settings = make_settings()
    result = run_simulation(
        settings, scenario_id="typical", soc_frac=0.5, now=NOW, tz=ADELAIDE
    )
    assert result["solver_status"].startswith("optimal")
    horizon_steps = settings.optimizer.horizon_hours * 2
    assert abs(len(result["intervals"]) - horizon_steps) <= 1
    assert result["meta"]["simulated"] is True
    assert result["meta"]["scenario"] == "typical"
    # every interval carries the fields the dashboard renders
    iv = result["intervals"][0]
    assert {"buy", "sell", "pv_kw", "load_kw", "soc_start", "action"} <= set(iv)


def test_every_scenario_solves():
    settings = make_settings()
    for sid in SCENARIOS:
        r = run_simulation(settings, scenario_id=sid, soc_frac=0.6, now=NOW, tz=ADELAIDE)
        assert r["solver_status"].startswith("optimal"), sid


def test_min_battery_export_price_override_suppresses_low_price_export():
    # cheap_overnight has a pricey evening (feed-in ~0.30) and cheap nights;
    # a floor above the evening feed-in should cut battery export there.
    settings = make_settings()
    base = run_simulation(settings, scenario_id="cheap_overnight", soc_frac=0.9,
                          now=NOW, tz=ADELAIDE)
    floored = run_simulation(settings, scenario_id="cheap_overnight", soc_frac=0.9,
                             now=NOW, tz=ADELAIDE,
                             overrides=SimOverrides(min_battery_export_price=0.50))
    def batt_export(r):
        return sum(iv["grid_export_kw"] for iv in r["intervals"] if iv["action"] == "discharge")

    assert batt_export(floored) < batt_export(base)


def test_api_scenarios_and_simulate(tmp_path):
    controller = _controller(tmp_path, make_settings())
    client = TestClient(create_app(AppState(), controller))

    scenarios = client.get("/api/scenarios").json()["scenarios"]
    assert scenarios

    resp = client.post("/api/simulate", json={"scenario": "evening_spike", "soc_frac": 0.4})
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["simulated"] is True
    assert len(body["intervals"]) > 10


def test_api_simulate_unknown_scenario_is_400(tmp_path):
    client = TestClient(create_app(AppState(), _controller(tmp_path, make_settings())))
    assert client.post("/api/simulate", json={"scenario": "nope"}).status_code == 400


def test_api_simulate_requires_config(tmp_path):
    # no settings saved -> controller.current is None
    client = TestClient(create_app(AppState(), _controller(tmp_path)))
    resp = client.post("/api/simulate", json={"scenario": "typical"})
    assert resp.status_code == 409


def test_api_simulate_accepts_overrides(tmp_path):
    client = TestClient(create_app(AppState(), _controller(tmp_path, make_settings())))
    resp = client.post(
        "/api/simulate",
        json={"scenario": "typical", "soc_frac": 0.5,
              "overrides": {"wear_cost_per_kwh": 0.03, "min_battery_export_price": 0.13}},
    )
    assert resp.status_code == 200
    assert resp.json()["solver_status"].startswith("optimal")

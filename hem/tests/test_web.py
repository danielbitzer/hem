from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from hem.models import Action, Plan, PlanInterval
from hem.web.app import AppState, create_app

NOW = datetime(2026, 7, 15, 11, 36, 30, tzinfo=UTC)


def sample_plan() -> Plan:
    iv = PlanInterval(
        start=NOW,
        end=NOW + timedelta(minutes=30),
        action=Action.DISCHARGE,
        power_kw=-3.2,
        soc_start=9.0,
        soc_end=7.3,
        buy=0.44,
        sell=0.16,
        pv_kw=0.0,
        load_kw=1.7,
        grid_import_kw=0.0,
        grid_export_kw=1.5,
        interval_cost=-0.12,
    )
    return Plan(
        intervals=[iv], objective_cost=-1.5, solver_status="optimal", solve_ms=18.0, computed_at=NOW
    )


def test_health_and_plan_endpoints():
    state = AppState()
    client = TestClient(create_app(state))

    assert client.get("/health").status_code == 200  # startup grace period
    assert client.get("/api/plan").status_code == 404

    state.plan = sample_plan()
    state.health.mark_success()
    body = client.get("/api/plan").json()
    assert body["solver_status"] == "optimal"
    assert body["intervals"][0]["action"] == "discharge"
    assert body["intervals"][0]["power_kw"] == -3.2


def test_index_and_static_served():
    client = TestClient(create_app(AppState()))
    index = client.get("/")
    assert index.status_code == 200
    assert "Home Energy Manager" in index.text
    assert './static/apexcharts.min.js' in index.text
    js = client.get("/static/apexcharts.min.js")
    assert js.status_code == 200
    assert "ApexCharts" in js.text[:200]

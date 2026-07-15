"""Ingress web app.

/health for the Supervisor watchdog, /api/plan for the latest plan, and a
placeholder page (charts arrive in Phase 5). All URLs must stay relative so
the page works unchanged behind HA ingress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from hem import __version__
from hem.models import Plan

HEALTHY_WINDOW = timedelta(minutes=15)


@dataclass
class HealthState:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_success: datetime | None = None
    last_error: str = ""

    def mark_success(self) -> None:
        self.last_success = datetime.now(UTC)
        self.last_error = ""

    def mark_error(self, error: str) -> None:
        self.last_error = error

    @property
    def healthy(self) -> bool:
        # Grace period after startup so the watchdog doesn't kill us before the
        # first cycle completes.
        ref = self.last_success or self.started_at
        return datetime.now(UTC) - ref < HEALTHY_WINDOW


@dataclass
class AppState:
    health: HealthState = field(default_factory=HealthState)
    plan: Plan | None = None


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="HEM", version=__version__)
    health = state.health

    @app.get("/api/plan")
    async def api_plan() -> JSONResponse:
        if state.plan is None:
            return JSONResponse({"error": "no plan computed yet"}, status_code=404)
        plan = state.plan
        return JSONResponse(
            {
                "computed_at": plan.computed_at.isoformat(),
                "solver_status": plan.solver_status,
                "solve_ms": plan.solve_ms,
                "objective_cost": plan.objective_cost,
                "intervals": [
                    {
                        "start": iv.start.isoformat(),
                        "end": iv.end.isoformat(),
                        "action": iv.action.value,
                        "power_kw": iv.power_kw,
                        "soc_start": iv.soc_start,
                        "soc_end": iv.soc_end,
                        "buy": iv.buy,
                        "sell": iv.sell,
                        "pv_kw": iv.pv_kw,
                        "load_kw": iv.load_kw,
                        "grid_import_kw": iv.grid_import_kw,
                        "grid_export_kw": iv.grid_export_kw,
                        "interval_cost": iv.interval_cost,
                    }
                    for iv in plan.intervals
                ],
            }
        )

    @app.get("/health")
    async def health_endpoint() -> JSONResponse:
        body = {
            "healthy": health.healthy,
            "last_success": health.last_success.isoformat() if health.last_success else None,
            "last_error": health.last_error,
        }
        return JSONResponse(body, status_code=200 if health.healthy else 503)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        status = "ok" if health.healthy else "degraded"
        last = "never"
        if health.last_success:
            last = health.last_success.strftime("%Y-%m-%d %H:%M:%S UTC")
        return f"""<!doctype html>
<title>HEM</title>
<h1>Home Energy Manager v{__version__}</h1>
<p>Status: <strong>{status}</strong> — last successful cycle: {last}</p>
<p>Plan charts arrive in a later phase.</p>
"""

    return app

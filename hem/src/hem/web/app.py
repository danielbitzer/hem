"""Ingress web app.

/health for the Supervisor watchdog, /api/plan for the latest plan, and a
placeholder page (charts arrive in Phase 5). All URLs must stay relative so
the page works unchanged behind HA ingress.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hem import __version__
from hem.models import Plan

HEALTHY_WINDOW = timedelta(minutes=15)
STATIC_DIR = Path(__file__).parent / "static"


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
    # cycle metadata for the dashboard: capacity_kwh, price_forecast_end, coverage
    meta: dict = field(default_factory=dict)


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
                "meta": state.meta,
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

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

"""Append-only JSONL recording of each cycle's normalized inputs (and later,
plans) to /data/history/YYYY-MM-DD.jsonl — the raw material for the Phase 3
backtester. One line per record: {"ts": ..., "kind": ..., "data": {...}}.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hem.models import PriceForecast, Series

DEFAULT_DIR = Path("/data/history")


def series_to_json(series: Series) -> dict[str, Any]:
    return {
        "times": [t.isoformat() for t in series.times],
        "values": series.values,
    }


def series_from_json(data: dict[str, Any]) -> Series:
    return Series(
        times=[datetime.fromisoformat(t) for t in data["times"]],
        values=[float(v) for v in data["values"]],
    )


def cycle_inputs_to_json(data: Any) -> dict[str, Any]:
    """Serialize a planner CycleData's normalized inputs for backtesting."""
    grid_starts = [s.start.isoformat() for s in data.grid.steps]
    return {
        "prices": prices_to_json(data.prices),
        "grid_starts": grid_starts,
        "dt_hours": [float(v) for v in data.inputs.dt_hours],
        "buy": [float(v) for v in data.inputs.buy],
        "sell": [float(v) for v in data.inputs.sell],
        "pv_kw": [float(v) for v in data.inputs.pv],
        "load_kw": [float(v) for v in data.inputs.load],
        "soc_frac": data.battery.soc_frac,
        "battery_power_kw": data.battery.power_kw,
    }


def prices_to_json(prices: PriceForecast) -> dict[str, Any]:
    return {
        "buy": series_to_json(prices.buy),
        "sell": series_to_json(prices.sell),
        "current_buy": prices.current_buy,
        "current_sell": prices.current_sell,
        "live_spike": prices.live_spike,
        "updated_at": prices.updated_at.isoformat() if prices.updated_at else None,
    }


class Recorder:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self._dir = directory

    def record(self, kind: str, data: dict[str, Any], ts: datetime | None = None) -> None:
        ts = ts or datetime.now(UTC)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{ts.date().isoformat()}.jsonl"
        line = json.dumps({"ts": ts.isoformat(), "kind": kind, "data": data})
        with path.open("a") as f:
            f.write(line + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self._dir.exists():
            return records
        for path in sorted(self._dir.glob("*.jsonl")):
            with path.open() as f:
                records.extend(json.loads(line) for line in f if line.strip())
        return records

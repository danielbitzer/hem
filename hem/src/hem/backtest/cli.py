"""Backtest CLI: replay recorded history through HEM and the baselines.

    uv run python -m hem.backtest.cli --history ./history --options ./dev-options.json

On the add-on, history lives in /data/history (copy it off with the
Samba/SSH add-on, or run this inside the container).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hem.backtest.policies import HemPolicy, NoBatteryPolicy, SelfConsumptionPolicy
from hem.backtest.sim import CycleRecord, SimResult, simulate
from hem.config import EnvSettings, load_settings
from hem.optimizer.model import GridParams
from hem.planner import battery_params
from hem.recorder import Recorder


def load_records(history_dir: Path) -> list[CycleRecord]:
    raw = Recorder(history_dir).read_all()
    records = [CycleRecord.from_json(r) for r in raw if r["kind"] == "inputs"]
    records.sort(key=lambda r: r.ts)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="HEM backtest")
    parser.add_argument("--history", type=Path, default=Path("/data/history"))
    parser.add_argument("--options", type=Path, default=None)
    parser.add_argument("--soc0", type=float, default=0.5, help="starting SoC fraction")
    args = parser.parse_args()

    settings = load_settings(args.options or EnvSettings().options_file)
    battery = battery_params(settings)
    grid = GridParams(
        import_limit_kw=settings.grid.import_limit_kw,
        export_limit_kw=settings.grid.export_limit_kw,
    )
    records = load_records(args.history)
    if len(records) < 12:
        raise SystemExit(f"only {len(records)} recorded cycles in {args.history}")
    first, last = records[0].ts, records[-1].ts
    print(f"{len(records)} cycles: {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M} UTC")

    soc0 = args.soc0 * battery.capacity_kwh
    policies = [
        NoBatteryPolicy(),
        SelfConsumptionPolicy(),
        HemPolicy(battery, grid, settings.spike.reserve_penalty_per_kwh),
    ]
    results: list[SimResult] = [
        simulate(p, records, battery, settings.grid.export_limit_kw, soc0) for p in policies
    ]

    baseline = next(r for r in results if r.policy == "self-consumption")
    print(f"\n{'policy':<18}{'total $':>10}{'$/day':>10}{'vs self-cons':>14}{'spike rev $':>13}")
    for r in results:
        uplift = baseline.total_cost - r.total_cost
        print(
            f"{r.policy:<18}{r.total_cost:>10.2f}{r.cost_per_day:>10.2f}"
            f"{uplift:>+14.2f}{r.spike_revenue():>13.2f}"
        )
    hem = next(r for r in results if r.policy == "hem")
    if hem.total_cost > baseline.total_cost:
        print("\nWARNING: HEM lost to self-consumption — investigate before enabling write mode.")


if __name__ == "__main__":
    main()

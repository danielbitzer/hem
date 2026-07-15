from datetime import UTC, datetime
from pathlib import Path

from conftest import load_fixture_state

from hem.adapters.amber import parse_forecast_attribute
from hem.recorder import Recorder, series_from_json, series_to_json


def test_series_json_roundtrip():
    series = parse_forecast_attribute(load_fixture_state("amber_express_feed_in_price"))
    restored = series_from_json(series_to_json(series))
    assert restored.times == series.times
    assert restored.values == series.values


def test_recorder_appends_daily_files(tmp_path: Path):
    rec = Recorder(tmp_path)
    day1 = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)
    day2 = datetime(2026, 7, 16, 11, 0, tzinfo=UTC)
    rec.record("inputs", {"x": 1}, ts=day1)
    rec.record("inputs", {"x": 2}, ts=day1)
    rec.record("plan", {"y": 3}, ts=day2)

    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == [
        "2026-07-15.jsonl",
        "2026-07-16.jsonl",
    ]
    records = rec.read_all()
    assert [r["data"] for r in records] == [{"x": 1}, {"x": 2}, {"y": 3}]
    assert records[2]["kind"] == "plan"


def test_read_all_empty_dir(tmp_path: Path):
    assert Recorder(tmp_path / "nope").read_all() == []

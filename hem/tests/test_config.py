import json
from pathlib import Path

import pytest

from hem.config import load_settings, resolve_connection

MINIMAL_OPTIONS = {
    "entities": {
        "buy_price": "sensor.amber_express_home_general_price",
        "sell_price": "sensor.amber_express_home_feed_in_price",
        "pv_forecast_today": "sensor.energy_production_today",
        "pv_forecast_tomorrow": "sensor.energy_production_tomorrow",
        "battery_soc": "sensor.battery_level",
        "battery_power": "sensor.battery_power",
        "weather": "weather.henley_beach_hourly",
    },
    "battery": {"capacity_kwh": 12.8, "max_charge_kw": 5.0, "max_discharge_kw": 5.0},
    "grid": {"import_limit_kw": 15.0, "export_limit_kw": 5.0},
    "load_profile": {"weekday_kw": [0.5] * 24, "weekend_kw": [0.6] * 24},
}


def write_options(tmp_path: Path, options: dict) -> Path:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    return path


def test_load_minimal_options(tmp_path: Path):
    settings = load_settings(write_options(tmp_path, MINIMAL_OPTIONS))
    assert settings.price_source == "amber_express"
    assert settings.control.mode == "dry_run"
    assert settings.optimizer.horizon_hours == 36
    assert settings.battery.soc_min == 0.10
    # forecast entities default to the price sensors (amber_express layout)
    assert settings.entities.buy_forecast == settings.entities.buy_price
    assert settings.entities.sell_forecast == settings.entities.sell_price


def test_explicit_forecast_entities_kept(tmp_path: Path):
    options = json.loads(json.dumps(MINIMAL_OPTIONS))
    options["entities"]["buy_forecast"] = "sensor.amber_general_forecast"
    settings = load_settings(write_options(tmp_path, options))
    assert settings.entities.buy_forecast == "sensor.amber_general_forecast"
    assert settings.entities.sell_forecast == settings.entities.sell_price


def test_invalid_soc_bounds_rejected(tmp_path: Path):
    options = json.loads(json.dumps(MINIMAL_OPTIONS))
    options["battery"]["soc_min"] = 0.9
    options["battery"]["soc_max"] = 0.5
    with pytest.raises(ValueError, match="soc_min"):
        load_settings(write_options(tmp_path, options))


def test_load_profile_must_have_24_values(tmp_path: Path):
    options = json.loads(json.dumps(MINIMAL_OPTIONS))
    options["load_profile"]["weekday_kw"] = [0.5] * 23
    with pytest.raises(ValueError):
        load_settings(write_options(tmp_path, options))


def test_missing_options_file_message(tmp_path: Path):
    with pytest.raises(RuntimeError, match="Options file not found"):
        load_settings(tmp_path / "nope.json")


def test_connection_supervisor():
    conn = resolve_connection({"SUPERVISOR_TOKEN": "tok"})
    assert conn.rest_url == "http://supervisor/core/api"
    assert conn.ws_url == "ws://supervisor/core/websocket"
    assert conn.token == "tok"


def test_connection_standalone():
    conn = resolve_connection(
        {"HEM_HA_URL": "http://homeassistant.local:8123/", "HEM_HA_TOKEN": "tok"}
    )
    assert conn.rest_url == "http://homeassistant.local:8123/api"
    assert conn.ws_url == "ws://homeassistant.local:8123/api/websocket"


def test_connection_standalone_https():
    conn = resolve_connection({"HEM_HA_URL": "https://ha.example.com", "HEM_HA_TOKEN": "tok"})
    assert conn.ws_url == "wss://ha.example.com/api/websocket"


def test_connection_unconfigured():
    with pytest.raises(RuntimeError, match="HEM_HA_URL"):
        resolve_connection({})

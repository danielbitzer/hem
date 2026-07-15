import json
from pathlib import Path

import pytest

from hem.config import EnvSettings, load_settings, resolve_connection, resolve_data_dir

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


def env_settings(**kwargs) -> EnvSettings:
    return EnvSettings(_env_file=None, **kwargs)  # hermetic: ignore any real .env


def test_connection_supervisor():
    conn = resolve_connection(env_settings(), supervisor_token="tok")
    assert conn.rest_url == "http://supervisor/core/api"
    assert conn.ws_url == "ws://supervisor/core/websocket"
    assert conn.token == "tok"


def test_connection_standalone():
    env = env_settings(ha_url="http://homeassistant.local:8123/", ha_token="tok")
    conn = resolve_connection(env, supervisor_token="")
    assert conn.rest_url == "http://homeassistant.local:8123/api"
    assert conn.ws_url == "ws://homeassistant.local:8123/api/websocket"


def test_connection_standalone_https():
    env = env_settings(ha_url="https://ha.example.com", ha_token="tok")
    conn = resolve_connection(env, supervisor_token="")
    assert conn.ws_url == "wss://ha.example.com/api/websocket"


def test_connection_unconfigured():
    with pytest.raises(RuntimeError, match="HEM_HA_URL"):
        resolve_connection(env_settings(), supervisor_token="")


def test_data_dir_resolution(tmp_path: Path):
    assert resolve_data_dir(env_settings(), supervisor_token="tok") == Path("/data")
    assert resolve_data_dir(env_settings(), supervisor_token="") == Path("data")
    custom = env_settings(data_dir=tmp_path)
    assert resolve_data_dir(custom, supervisor_token="") == tmp_path


def test_env_settings_reads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text(
        "HEM_HA_URL=http://ha.local:8123\nHEM_HA_TOKEN=tok\nHEM_OPTIONS_FILE=./dev-options.json\n"
    )
    monkeypatch.chdir(tmp_path)
    env = EnvSettings()
    assert env.ha_url == "http://ha.local:8123"
    assert env.options_file == Path("./dev-options.json")

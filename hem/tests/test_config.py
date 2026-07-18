import json
from pathlib import Path

import pytest

from hem.config import EnvSettings, Settings, resolve_connection, resolve_log_level
from hem.config_store import ConfigStore

MINIMAL_CONFIG = {
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
}


def make_settings(**overrides) -> Settings:
    return Settings.model_validate({**MINIMAL_CONFIG, **overrides})


def test_minimal_config_defaults():
    settings = make_settings()
    assert settings.optimizer.horizon_hours == 36
    assert settings.battery.soc_min == 0.10
    # HEM starts disabled: the enable toggle in the UI is a deliberate act
    assert settings.enabled is False
    # no load sensor is a valid (degraded) config — HEM plans with zero load
    assert settings.entities.load_power == ""


def test_invalid_soc_bounds_rejected():
    config = json.loads(json.dumps(MINIMAL_CONFIG))
    config["battery"]["soc_min"] = 0.9
    config["battery"]["soc_max"] = 0.5
    with pytest.raises(ValueError, match="soc_min"):
        Settings.model_validate(config)


def test_store_roundtrip_and_backup(tmp_path: Path):
    store = ConfigStore(tmp_path / "hem-config.json")
    assert store.load() is None  # unconfigured

    store.save(make_settings(enabled=True))
    doc = json.loads(store.path.read_text())
    assert doc["schema_version"] == 1
    loaded = store.load()
    assert loaded is not None and loaded.enabled is True

    store.save(make_settings(enabled=False))
    assert store.load().enabled is False
    # previous version preserved
    bak = json.loads((tmp_path / "hem-config.json.bak").read_text())
    assert bak["config"]["enabled"] is True


def test_store_corrupt_file_is_unconfigured_not_fatal(tmp_path: Path):
    path = tmp_path / "hem-config.json"
    path.write_text("{not json")
    assert ConfigStore(path).load() is None
    path.write_text(json.dumps({"schema_version": 1, "config": {"battery": {}}}))
    assert ConfigStore(path).load() is None  # invalid settings, same story


def test_unknown_keys_ignored():
    # a stale key from an older config version is ignored, not fatal
    config = json.loads(json.dumps(MINIMAL_CONFIG))
    config["entities"]["buy_forecast"] = "sensor.amber_general_forecast"
    Settings.model_validate(config)


def env_settings(**kwargs) -> EnvSettings:
    return EnvSettings(_env_file=None, **kwargs)  # hermetic: ignore any real .env


def test_log_level_env_wins(monkeypatch: pytest.MonkeyPatch):
    assert resolve_log_level(env_settings(log_level="debug")) == "debug"


def test_log_level_defaults_to_info_without_options_file():
    assert resolve_log_level(env_settings()) == "info"


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


def test_env_settings_reads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text(
        "HEM_HA_URL=http://ha.local:8123\nHEM_HA_TOKEN=tok\nHEM_CONFIG_FILE=./hem-config.json\n"
    )
    monkeypatch.chdir(tmp_path)
    env = EnvSettings()
    assert env.ha_url == "http://ha.local:8123"
    assert env.config_file == Path("./hem-config.json")

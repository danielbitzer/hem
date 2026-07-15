# HEM — Home Energy Manager

A Home Assistant add-on that optimizes home battery charge/discharge and solar-export
decisions against Amber Electric's 5-minute wholesale pricing, using a rolling-horizon
MILP (CVXPY + HiGHS) re-solved every 5 minutes.

Inputs (all via existing HA integrations — no glue automations needed):

- **Prices**: [Amber Express](https://github.com/hass-energy/amber-express) (recommended)
  or the core `amberelectric` integration
- **Solar forecast**: [Open-Meteo Solar Forecast](https://github.com/rany2/ha-open-meteo-solar-forecast)
- **Battery/inverter**: Sungrow SHx via the
  [mkaiser Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)
- **Temperature forecast**: any HA `weather.*` entity (drives the rule-based load forecast)

Outputs (dry-run mode): `sensor.hem_action`, `sensor.hem_power_setpoint`,
`sensor.hem_soc_target`, `sensor.hem_horizon_cost`, `sensor.hem_plan`, `sensor.hem_status`.
Write-mode control of the inverter is planned behind a config switch (see
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)).

## Install (HA OS / Supervised)

Settings → Add-ons → Add-on store → ⋮ → Repositories → add this repo's URL, then
install **Home Energy Manager**. Or copy the `hem/` directory to `/addons/` for a
local build.

## Development (no Home Assistant OS required)

Run directly against any HA instance with a long-lived access token:

```sh
cd hem
uv sync
HEM_HA_URL=http://homeassistant.local:8123 \
HEM_HA_TOKEN=<long-lived token> \
HEM_OPTIONS_FILE=./dev-options.json \
uv run python -m hem
```

or via Docker: `docker compose -f docker-compose.dev.yml up --build`
(reads the same env vars from `.env`).

Tests: `cd hem && uv run pytest`

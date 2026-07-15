# HEM — Home Energy Manager

A Home Assistant add-on that optimizes home battery charge/discharge and solar-export
decisions against Amber Electric's 5-minute wholesale pricing, using a rolling-horizon
MILP (CVXPY + HiGHS) re-solved every 5 minutes.

Inputs (all via existing HA integrations — no glue automations needed):

- **Prices**: [Amber Express](https://github.com/hass-energy/amber-express)
  (set to advanced-price mode; the core `amberelectric` integration is not supported)
- **Solar forecast**: [Open-Meteo Solar Forecast](https://github.com/rany2/ha-open-meteo-solar-forecast)
- **Battery/inverter**: Sungrow SHx via the
  [mkaiser Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)
- **Temperature forecast**: any HA `weather.*` entity (drives the rule-based load forecast)

Outputs (dry-run mode, the default): `sensor.hem_action`, `sensor.hem_power_setpoint`,
`sensor.hem_soc_target`, `sensor.hem_horizon_cost`, `sensor.hem_plan`, `sensor.hem_status`,
plus an ingress dashboard. Setting `control.mode: active` makes HEM drive the inverter —
read the active-mode checklist in [hem/DOCS.md](hem/DOCS.md) first (watchdog blueprint,
override helper, entity verification).

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

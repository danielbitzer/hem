# HEM — Home Energy Manager

A Home Assistant add-on that optimizes home battery charge/discharge and
solar-export decisions against Amber Electric's 5-minute wholesale pricing.
Every 5 minutes it re-solves a mixed-integer linear program (MILP) over the
next ~36 hours and publishes what the battery should do *right now* — classic
receding-horizon MPC, tuned for one job: **capture price spikes without
trusting price forecasts too much**.

HEM is a **recommendation engine**. It never touches your inverter: it
publishes sensors, and actuation happens through a Home Assistant automation
you own (built from a shipped blueprint, with a heartbeat failsafe). That
makes it inverter-agnostic — anything HA can control can follow the plan.

**[→ Setup guide from a fresh HA install](docs/SETUP.md)** ·
**[→ Add-on docs / option reference](hem/DOCS.md)**

## Inputs

All via existing HA integrations — no glue automations needed:

- **Prices**: [Amber Express](https://github.com/hass-energy/amber-express) in
  advanced-price mode — HEM parses its `forecast` attribute (Amber's own
  SmartShift prediction) and the live price-spike binary sensor. The core
  `amberelectric` integration is not supported.
- **Solar forecast**: [Open-Meteo Solar Forecast](https://github.com/rany2/ha-open-meteo-solar-forecast)
  (`watts` attribute, 15-min resolution).
- **Battery**: any integration exposing SoC and battery power, e.g. Sungrow
  SHx via the [mkaiser Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant).
- **Load**: learned daily from your actual consumption — hour-of-day averages
  from months of long-term statistics of a house-load sensor, plus an
  optional learned temperature response (kW per degree of cooling/heating)
  applied to the forecast temps from any hourly `weather.*` entity. No load
  sensor → HEM plans with zero load and warns on the dashboard.

## How the optimizer works

Each cycle: **gather** entity states → **normalize** onto a shared time grid →
**solve** the MILP → **publish** the plan. The grid follows the forecast's
native boundaries (5-min intervals near-term, 30-min beyond, a fractional
first step from *now*), so no forecast information is smeared by resampling.

Per step, the decision variables are battery charge/discharge power, grid
import/export, and PV curtailment, subject to:

- power balance (PV + discharge + import = load + charge + export)
- SoC dynamics with charge/discharge efficiency, SoC min/max bounds
- battery power limits, grid connection import/export limits
- no simultaneous charge & discharge (the binary variables that make it a MILP)

The objective minimizes the horizon energy bill plus a **battery wear cost**
per discharged kWh, minus a **terminal value** on energy left in the battery at
the horizon (default: median buy price × efficiency − wear, so the battery
isn't dumped at any positive price just because the horizon ends). Spike
capture, curtailment under negative feed-in, and charging on negative prices
all fall out of the economics rather than hand-written rules.

Because Amber forecasts are routinely wrong about spikes, several layers keep
the plan honest:

- **Spike reserve**: while a high forecast price sits within the lookahead
  window, a soft SoC floor holds energy ready to sell — soft, so a genuinely
  better opportunity can still break it. It triggers on raw forecast prices.
- **Forecast haircut**: above-median sell prices beyond ~6h are discounted
  toward the median in the objective, so phantom distant spikes don't distort
  near-term decisions.
- **Event-triggered re-solve**: a WebSocket watcher re-solves within seconds
  when the live price moves ≥ $0.05 or the spike sensor flips — a confirmed
  spike gets a full-power discharge decision immediately, optionally at a
  raised spike-only discharge cap (`spike.discharge_kw`).
- **Never grid-charge during a confirmed spike**, as a hard guard on top of
  the economics.
- **Hysteresis**: the current action only switches if the switch improves the
  full horizon objective by more than a threshold (solved pin-and-compare), so
  near-degenerate solutions don't chatter the inverter.
- **Fallback**: solver failure reuses the previous plan shifted forward;
  stale inputs degrade to idle recommendations, never silent garbage.

## Outputs

Published every cycle (REST sensors): `sensor.hem_action`
(charge/discharge/idle/curtail), `sensor.hem_power_setpoint` (signed kW, with
`power_w` attribute), `sensor.hem_soc_target`, `sensor.hem_horizon_cost`,
and `sensor.hem_status` (heartbeat).
An ingress dashboard charts the plan: prices, PV/load forecasts, planned
battery power, and the SoC trajectory.

Actuation = your automation from
[blueprints/hem_actuator.yaml](blueprints/hem_actuator.yaml): it maps
action + setpoint onto your inverter's controls, and reverts to
self-consumption when HEM's heartbeat goes stale. See
[hem/DOCS.md](hem/DOCS.md) for a complete Sungrow example.

## Backtesting

Dry-run cycles record every input and decision to `/data/history/` as JSONL.
The backtester replays them receding-horizon (re-solving each step with only
the data that was available at that moment — same code paths as live) against
baseline policies:

```sh
cd hem && uv run python -m hem.backtest.cli --history ./data/history --options ./dev-options.json
```

reporting $/day for no-battery, naive self-consumption, and HEM, plus revenue
earned during spikes. The house rule: **HEM must beat self-consumption on
your own recorded data before you create the actuator automation.**

## Under the hood

| | |
|---|---|
| Optimization | [CVXPY](https://www.cvxpy.org/) (`cvxpy-base`) + [HiGHS](https://highs.dev/) (`highspy`) — ~70 binaries/solve, tens of ms |
| Numerics | numpy (no pandas; the time-grid resampler is ~50 lines) |
| HA I/O | aiohttp — REST for states/publishing, WebSocket for event-triggered re-solves |
| Config | pydantic + pydantic-settings (add-on options JSON / `HEM_*` env) |
| Dashboard | FastAPI + uvicorn behind HA ingress, vendored ApexCharts |
| Packaging | uv-locked deps; Debian-based image (cvxpy has no musl wheels); multi-arch (amd64/aarch64) prebuilt via GitHub Actions → GHCR |

Layout: the repo root is an HA add-on repository; the add-on and all Python
lives in [hem/](hem/) (`src/hem/` — adapters, timegrid, optimizer, planner,
publisher, backtest, web), with the actuator blueprint in
[blueprints/](blueprints/).

## Install (HA OS / Supervised)

Settings → Add-ons → Add-on store → ⋮ → Repositories → add this repo's URL,
then install **Home Energy Manager**. Prebuilt images are pulled from GHCR
(maintainer note: after the first CI publish, the `hem-amd64`/`hem-aarch64`
packages must be set to public on GitHub or installs can't pull them).
Full walkthrough including the input integrations: [docs/SETUP.md](docs/SETUP.md).

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

or via Docker, from the **repo root**: `docker compose -f docker-compose.dev.yml up --build`
(reads `HEM_HA_URL`/`HEM_HA_TOKEN` from your shell environment or a repo-root
`.env`; note the standalone run above uses `hem/.env` instead).

Tests: `cd hem && uv run pytest`

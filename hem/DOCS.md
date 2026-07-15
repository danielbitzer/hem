# Home Energy Manager (HEM)

HEM reads Amber Electric prices, an Open-Meteo solar forecast, your battery state, and a
temperature forecast from existing Home Assistant integrations, then solves a
mixed-integer optimization every 5 minutes to plan battery charge/discharge and solar
export over the next ~36 hours.

In **dry-run mode** (the default) HEM only publishes its recommendations as sensors —
nothing is written to your inverter.

## Prerequisites

- [Amber Express](https://github.com/hass-energy/amber-express) with its pricing mode
  set to **advanced price** (the core Amber Electric integration is not supported —
  its forecast attribute has 1c price resolution and no advanced-price mode)
- [Open-Meteo Solar Forecast](https://github.com/rany2/ha-open-meteo-solar-forecast)
- Battery SoC/power sensors (e.g. the
  [mkaiser Sungrow Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant))
- Any `weather.*` entity with hourly forecasts

## Configuration

### `entities`

Point each option at your entity IDs. Amber Express's forecast attributes live on
the price sensors themselves, so `buy_forecast`/`sell_forecast` can normally be left
empty (they default to the price sensors).

### `battery`

Physical parameters of your battery. `wear_cost_per_kwh` is the degradation cost charged
against every discharged kWh in the objective — a reasonable starting point is battery
replacement cost divided by total lifetime throughput (e.g. $6000 / 38 MWh ≈ $0.16, or
much lower if you expect the battery to outlive its warranty).

### `grid`

Limits of your **grid connection**, distinct from the battery's power limits:

- `import_limit_kw` — maximum net draw from the grid for the whole house
  (connection/main-breaker capacity).
- `export_limit_kw` — maximum net feed-in allowed by your DNSP/connection
  agreement. This caps what actually reaches the grid regardless of how hard
  the battery discharges: export = battery discharge + PV − house load. If
  you raise `spike.discharge_kw`, raise this to match (if permitted) or the
  extra battery power has nowhere to go.

`battery.max_charge_kw` / `max_discharge_kw` limit the *battery* (cell wear,
inverter DC side); the grid limits cap the *net AC flow at the meter*. The
optimizer respects both simultaneously.

### `load_profile`

24 hourly baseline kW values for weekdays and weekends, plus temperature rules that add
heating/cooling load when the forecast temperature crosses a threshold.

### `optimizer`

- `horizon_hours` (36) — how far ahead each plan looks. Longer sees more of
  tomorrow's solar; beyond the price forecast the tail is padded (shaded on
  the dashboard).
- `terminal_soc_value` — how leftover stored energy is valued at the horizon
  end, in **$/kWh** (it is NOT a target SoC). `auto` = median buy price ×
  discharge efficiency − wear cost, i.e. "what buying that energy later would
  plausibly cost". Without it the optimizer would dump the battery at any
  positive price before the horizon.
- `solver_timeout_s` (30, max 60) — HiGHS time limit per solve; normal solves
  take tens of milliseconds.
- `action_switch_threshold_dollars` (0.02) — hysteresis: the current action
  only changes if switching improves the horizon objective by more than this.
- `forecast_haircut` (0.2) — fraction of the above-median excess shaved off
  sell prices more than 6 h out, so distant phantom spikes don't distort
  near-term decisions. The spike reserve reads raw prices, unaffected.

### `spike`

When Amber flags a potential price spike within `lookahead_hours`, HEM softly reserves
`reserve_kwh` in the battery so it can sell into the spike if it confirms.

`discharge_kw` optionally raises the discharge cap **only while the spike binary
sensor confirms a spike, and only for the current interval** — everyday operation
keeps the wear-conscious `battery.max_discharge_kw`. Spikes are rare enough that
a few full-power hours add negligible cell wear while capturing peak revenue.
Set it to your inverter's true limit (0 disables). Note the extra power only
reaches the grid if `grid.export_limit_kw` allows it.

## Published sensors

| Entity | Meaning |
|---|---|
| `sensor.hem_status` | `ok` / `degraded`; heartbeat with solve stats |
| `sensor.hem_action` | recommended action now: charge / discharge / idle / curtail |
| `sensor.hem_power_setpoint` | recommended battery power, kW (+charge / −discharge) |
| `sensor.hem_soc_target` | planned SoC at end of the current interval |
| `sensor.hem_horizon_cost` | expected net cost ($) over the horizon |
| `sensor.hem_plan` | full interval-by-interval plan in the `plan` attribute |

These sensors are republished every cycle and disappear on HA restart until the next
cycle (~5 min). Exclude the plan sensor from the recorder to avoid database bloat:

```yaml
recorder:
  exclude:
    entities:
      - sensor.hem_plan
```

## Dashboard

The add-on serves an ingress panel (sidebar → Energy Manager): current
action/setpoint/SoC/cost tiles plus charts of prices, PV/load forecast, planned
battery power with grid flows, and the SoC trajectory. Auto-refreshes every
minute; fully offline (no CDN).

## Backtesting

Every cycle's normalized inputs are recorded to `/data/history/*.jsonl`. After
a few days of dry-run operation, replay them:

```sh
python -m hem.backtest.cli --history /data/history --options /data/options.json
```

This compares HEM against no-battery and self-consumption baselines and reports
$/day and spike revenue. **Do not enable active mode until HEM beats
self-consumption on your own recorded data.**

## Active mode (writes to the inverter)

`control.mode: active` makes HEM drive the inverter via the mkaiser package's
select/number entities. Before enabling:

1. Verify every entity ID and option string under `control.entities` against
   your install (they vary between package versions).
2. Create an `input_boolean.hem_override` helper — turning it on halts all
   HEM writes instantly.
3. Import the watchdog blueprint (`blueprints/hem_watchdog.yaml` in this repo)
   and create the automation: it reverts the inverter to self-consumption if
   HEM's heartbeat goes stale, even if the add-on dies uncleanly.

Guardrails: write-on-change only, rate-limited (`max_writes_per_hour`),
setpoints clamped to battery limits, self-consumption re-asserted on clean
shutdown.

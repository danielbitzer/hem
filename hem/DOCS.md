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

Set `source: history` to **learn the hourly baseline from your actual
consumption** instead: HEM reads recorder history of `entities.load_power` (a
house load sensor in W or kW — e.g. the mkaiser package's `sensor.load_power`)
over the last `history_days` (14) and builds time-weighted hour-of-day
averages, split weekday/weekend, in your local timezone. The learning refreshes
every 6 hours. The configured `weekday_kw`/`weekend_kw` remain the fallback —
per hour when a bucket has under 2 observed hours of data, and entirely when
history is unavailable — so a recorder purge can degrade the forecast but never
break planning.

Two caveats with `source: history`:

- The learned averages already include your typical heating/cooling, so keep
  `temp_rules` for extreme-day corrections only (or empty) to avoid counting
  the same aircon twice.
- Make sure the sensor is not excluded from the HA recorder, and that
  `history_days` fits inside your recorder purge window (default 10 days —
  `history_days` beyond it just learns from what exists).

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

## Controlling your inverter (the actuator automation)

HEM never writes to your inverter. It publishes recommendations; a Home
Assistant automation that **you** own turns them into control — inspectable,
traceable, editable, and disabling it is the master off-switch.

Import `blueprints/hem_actuator.yaml` from this repo (Settings → Automations →
Blueprints → Import), then create an automation from it. You supply three
action sequences for your hardware; inside them the variables `power_kw`
(signed, +charge/−discharge), `power_w` (magnitude in watts), and `action`
are available. The blueprint has the failsafe built in: if HEM's heartbeat is
stale, degraded, or the sensors are missing (HA restarted while HEM was
down), your *idle* actions run — so a dead add-on can never leave the
inverter stuck in forced mode. Keep the idle sequence simple and idempotent.

Example sequences for the mkaiser Sungrow package (verify entity IDs and
option strings against your install — they vary between package versions):

```yaml
# charge_actions
- action: number.set_value
  target: {entity_id: number.sungrow_battery_forced_charge_discharge_power}
  data: {value: "{{ power_w }}"}
- action: select.select_option
  target: {entity_id: select.sungrow_battery_forced_charge_discharge_cmd}
  data: {option: "Forced charge"}
- action: select.select_option
  target: {entity_id: select.sungrow_ems_mode}
  data: {option: "Forced mode"}

# discharge_actions — as above with option: "Forced discharge"

# idle_actions (also the failsafe — keep robust)
- action: select.select_option
  target: {entity_id: select.sungrow_battery_forced_charge_discharge_cmd}
  data: {option: "Stop (default)"}
- action: select.select_option
  target: {entity_id: select.sungrow_ems_mode}
  data: {option: "Self-consumption mode (default)"}
```

Set the power register **before** engaging forced mode (as above), so a
partial failure leaves the inverter in its previous mode rather than forced
with a stale setpoint.

**Do not create the automation until a backtest on your own recorded data
shows HEM beating self-consumption** (see Backtesting above), and bench-test
it: watch the inverter follow a charge → discharge → idle transition, then
stop the HEM add-on and confirm the failsafe reverts to self-consumption
within your configured heartbeat age.

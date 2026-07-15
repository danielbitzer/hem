# Home Energy Manager (HEM)

HEM reads Amber Electric prices, an Open-Meteo solar forecast, your battery state, and a
temperature forecast from existing Home Assistant integrations, then solves a
mixed-integer optimization every 5 minutes to plan battery charge/discharge and solar
export over the next ~36 hours.

In **dry-run mode** (the default) HEM only publishes its recommendations as sensors —
nothing is written to your inverter.

## Prerequisites

- [Amber Express](https://github.com/hass-energy/amber-express) (recommended; set its
  pricing mode to **advanced price**) or the core Amber Electric integration
- [Open-Meteo Solar Forecast](https://github.com/rany2/ha-open-meteo-solar-forecast)
- Battery SoC/power sensors (e.g. the
  [mkaiser Sungrow Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant))
- Any `weather.*` entity with hourly forecasts

## Configuration

### `entities`

Point each option at your entity IDs. For Amber Express the forecast attributes live on
the price sensors themselves, so `buy_forecast`/`sell_forecast` can be left empty (they
default to the price sensors). For the core Amber integration set them to the dedicated
Forecast sensors.

### `battery`

Physical parameters of your battery. `wear_cost_per_kwh` is the degradation cost charged
against every discharged kWh in the objective — a reasonable starting point is battery
replacement cost divided by total lifetime throughput (e.g. $6000 / 38 MWh ≈ $0.16, or
much lower if you expect the battery to outlive its warranty).

### `load_profile`

24 hourly baseline kW values for weekdays and weekends, plus temperature rules that add
heating/cooling load when the forecast temperature crosses a threshold.

### `spike`

When Amber flags a potential price spike within `lookahead_hours`, HEM softly reserves
`reserve_kwh` in the battery so it can sell into the spike if it confirms.

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

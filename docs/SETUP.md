# Setting up HEM from a fresh Home Assistant install

This walks from a clean Home Assistant OS install to HEM planning (dry-run),
and then — only after the backtest gate — to actual inverter control. Every
step before "Install the HEM add-on" is an ordinary HA integration that HEM
merely reads, so if you already have some of them, skip ahead.

What you'll end up with:

| Piece | Provides | Source |
|---|---|---|
| Amber Express | Buy/sell prices + Amber's advanced forecast + spike flag | HACS |
| Open-Meteo Solar Forecast | PV production forecast | HACS |
| A `weather.*` entity | Hourly temperature forecast (load rules) | Built-in |
| Battery integration | SoC %, battery power, (optional) house load | e.g. mkaiser Sungrow |
| **HEM add-on** | The optimizer + recommendation sensors + dashboard | this repo |
| Actuator automation | Turns recommendations into inverter control | blueprint, later |

## 1. Prerequisites

- Home Assistant OS or Supervised (the add-on needs the Supervisor). Amber
  Electric as your retailer, on wholesale pricing.
- [HACS](https://hacs.xyz/docs/use/download/download/) installed — both price
  and solar-forecast integrations come from it.
- An Amber API token: create one at
  [app.amber.com.au/developers](https://app.amber.com.au/developers).

## 2. Amber Express (prices)

HEM supports [Amber Express](https://github.com/hass-energy/amber-express)
only — the core `amberelectric` integration's forecasts have 1c resolution and
no advanced-price mode, which is not good enough to optimize against.

1. HACS → search for **Amber Express** (add
   `https://github.com/hass-energy/amber-express` as a custom repository if it
   isn't listed) → download, restart HA.
2. Settings → Devices & services → Add integration → Amber Express → paste your
   API token and pick your site.
3. **Set the pricing mode to "advanced price"** in the integration options.
   This makes the `forecast` attribute carry Amber's own SmartShift price
   prediction instead of raw AEMO forecasts, which over-predict spike duration
   by hours. HEM assumes this mode.
4. Note the entities it created — you'll need:
   - `sensor.amber_express_<site>_general_price` (buy)
   - `sensor.amber_express_<site>_feed_in_price` (sell)
   - `binary_sensor.amber_express_<site>_price_spike`

## 3. Open-Meteo Solar Forecast (PV)

1. HACS → **Open-Meteo Solar Forecast**
   ([rany2/ha-open-meteo-solar-forecast](https://github.com/rany2/ha-open-meteo-solar-forecast))
   → download, restart.
2. Add the integration with your latitude/longitude, panel declination (tilt),
   azimuth, and total DC kWp. If your array has multiple orientations, prefer
   one config entry that models the whole array (or sum per-plane sensors into
   template sensors) — HEM reads a single pair of entities.
3. Note `sensor.<name>_energy_production_today` and `..._tomorrow`. HEM uses
   their `watts` attribute (15-min resolution), not the state value.

## 4. Weather (temperature forecast)

Any `weather.*` entity that supports **hourly** forecasts works; the built-in
Met.no ("Forecast Home") entity does. HEM calls the `weather.get_forecasts`
service on it. Only temperature is used, and only by the load-profile
temperature rules — if the entity is unavailable HEM just plans without them.

## 5. Battery and inverter sensors

HEM needs two sensors from whatever integrates your battery:

- **SoC** in % (or 0–1)
- **battery power** in W or kW (units must be on the entity)

For Sungrow hybrids the established path is the
[mkaiser Sungrow Modbus package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant):
enable Modbus TCP on the inverter (or via the WiNet dongle), copy the package
YAML into your config, and you get `sensor.battery_level`,
`sensor.battery_power`, plus the control entities the actuator automation will
use later.

Two things to check:

- **Sign convention**: watch `sensor.battery_power` while the battery charges.
  If it reads *negative* while charging (mkaiser default), HEM's default
  `power_convention: charge_negative` is correct; if positive, set
  `charge_positive`.
- **Optional but recommended — house load sensor** (e.g. mkaiser's
  `sensor.load_power`): lets HEM learn your real hourly load profile from
  recorder history (`load_profile.source: history`) instead of hand-typed
  hourly values.

## 6. Install the HEM add-on

1. Settings → Add-ons → Add-on store → ⋮ → Repositories → add
   `https://github.com/danielbitzer/hem` → install **Home Energy Manager**.
2. Open the **Configuration** tab and fill in:
   - `entities.*` — the entity IDs from steps 2–5.
   - `battery.*` — capacity, charge/discharge limits (deliberately cap these
     below your inverter's capability to reduce cell wear; `spike.discharge_kw`
     can raise the cap during confirmed spikes only), efficiency, SoC bounds,
     wear cost.
   - `grid.*` — your connection's import limit and DNSP export limit.
   - `load_profile` — either type in hourly kW values, or set
     `source: history` + `entities.load_power` to learn them.
   - `spike.*` — the spike-reserve hedge; defaults are sane, see the
     Documentation tab.
3. Start the add-on and watch the log: you should see `cycle ok: action=...`
   within a minute. The **Energy Manager** sidebar item (ingress) shows the
   dashboard — plan, prices, PV/load forecast, SoC trajectory.
4. Check Developer tools → States for `sensor.hem_action`,
   `sensor.hem_power_setpoint`, `sensor.hem_soc_target`,
   `sensor.hem_horizon_cost`, `sensor.hem_plan`, `sensor.hem_status`.
5. Keep `sensor.hem_plan` (a large attribute republished every 5 minutes) out
   of the recorder database:

   ```yaml
   # configuration.yaml
   recorder:
     exclude:
       entities:
         - sensor.hem_plan
   ```

At this point HEM is a pure **recommendation engine** — it writes nothing to
the inverter, ever. It also records every cycle's inputs and plan to
`/data/history/` for the next step.

## 7. The backtest gate

Let dry-run record for **at least a week**, then replay HEM against baseline
policies on your own data:

```sh
# dev checkout on your machine (copy the add-on's /data/history locally, e.g.
# via the Samba/SSH add-on; standalone dev runs record to hem/data/history)
cd hem
uv run python -m hem.backtest.cli --history ./data/history
```

It reports $/day for no-battery, naive self-consumption, and HEM, plus the
spike capture rate. **Do not wire up actuation until HEM beats
self-consumption on your recorded data** — tune wear cost, spike reserve, and
load profile first if it doesn't.

## 8. Actuation (after the gate)

Import [`blueprints/hem_actuator.yaml`](../blueprints/hem_actuator.yaml)
(Settings → Automations → Blueprints → Import), create an automation from it,
and fill in the three action sequences — charge / discharge / idle — for your
hardware; a complete Sungrow (mkaiser) example lives in the add-on
Documentation tab ([hem/DOCS.md](../hem/DOCS.md)). The blueprint has a
heartbeat failsafe built in: if HEM stops publishing or reports degraded, your
idle sequence runs and the inverter returns to self-consumption.

Bench-test before trusting it: watch a charge → discharge → idle transition,
then stop the add-on and confirm the failsafe fires within its check interval.
Disabling the automation is always the master off-switch.

# Changelog

## Unreleased

- Dashboard updates now show without a force-refresh: `index.html` is served
  with `Cache-Control: no-cache` (ETag revalidation) so it always points at
  the current hashed bundle; the hashed assets themselves cache as immutable.
- Vacation mode dialog: the end-time picker is always pre-filled with a
  concrete suggestion (tomorrow, next full hour) and a "No end time" button
  replaces leave-it-empty, with a line stating exactly what will be saved.
  Safari renders an untouched `datetime-local` with today's date while its
  value is still empty — end times silently saved as "no end".

## 0.3.0

- Dashboard: tile "?" help tooltips are proper styled tooltips (shadcn) with
  keyboard focus support instead of native browser `title` bubbles (#11).

- **Re-solve on every price change**: the $0.05 significance threshold is
  gone — any change of the live buy/sell price (or its estimate flag)
  triggers an early re-solve, so the plan and dashboard reflect the real
  price within seconds of Amber confirming it instead of up to 5 minutes
  later. A 5 s floor between event-driven solves guards against a flapping
  sensor; the 5-minute boundary solve is unchanged. A spike_status flip on
  the spike sensor now also triggers, even before its binary state turns on.
- Dashboard: the Amber buy/sell tile is marked "forecast, unconfirmed" (with
  an explanatory tooltip) while the solve used Amber's estimate for the
  current interval — right at each 5-minute boundary, before the confirmed
  price lands and the re-solve clears it.

- **Vacation mode**: flatten the load forecast to a configured standby
  baseline while the household is away, freeing the whole battery for spikes
  and cheap windows. Enabled from a dialog at the top of Settings
  (baseline kW + optional local end time); auto-expires at the end time, and
  an end inside the horizon reverts later steps to the learned forecast so
  the return evening is already planned. No temperature response and no
  `load.buffer` while active. Surfaced as a dashboard banner and
  `binary_sensor.hem_vacation_mode` (visibility only — the actuator
  deliberately ignores it).

## 0.2.0

- **Configuration moves into the web UI** (#5): a new Settings view (shadcn
  UI + TanStack Form) with per-field inline documentation, searchable entity
  pickers fed by a new `/api/entities` endpoint, server-side validation with
  per-field errors, and save-and-apply without an add-on restart. HEM now
  owns its config at `/data/hem-config.json` (atomic writes, `.bak`,
  `schema_version`); the Supervisor options are reduced to `log_level` only.
  **Breaking**: existing installs must clear the old options from the add-on
  Configuration tab (⋮ → Edit in YAML, leave only `log_level`) and re-enter
  settings in the web UI — there is no migration. A new **HEM enabled**
  master switch (off on first boot / until configured) stops planning cycles
  and publishes `sensor.hem_status` as `disabled`/`unconfigured`, so the
  actuator blueprint's failsafe keeps the inverter in self-consumption;
  `/health` stays healthy in those states so the watchdog doesn't
  restart-loop a deliberately disabled add-on. Standalone dev uses
  `./hem-config.json` (via the same UI); `dev-options.json` and
  `HEM_OPTIONS_FILE` are gone.
- **`battery.daily_target_hour` is now `battery.daily_target_time`** (HH:MM,
  default 15:00): the daily full-charge target supports minutes and is a
  proper time picker in the Settings view.
- **`load.buffer`** (default 0): safety margin on the learned load forecast —
  the whole forecast (temperature response included) is scaled by
  `1 + buffer`, so 0.1 plans for 10% more house load everywhere. Shown on the
  dashboard's load-forecast line when active.

- **Dashboard rewritten in React** (#3): React 19 with the React Compiler,
  TypeScript, Recharts, Tailwind — built by Vite/Bun into the same fully
  offline ingress bundle. Feature parity with the old page (tiles, meta and
  load-forecast lines, warning banner, padded-tail band, all charts), plus
  the mode strip now joins the synced hover crosshair. The `/api/plan`
  contract is unchanged — now expressed as Zod schemas that validate every
  response, with polling handled by TanStack Query.

## 0.1.9

- **Soft daily SoC target** (`battery.daily_target_soc`, off by default):
  softly requires the battery at a target SoC by a local hour each day
  (default 3pm), paying at most `daily_target_penalty_per_kwh` ($0.10) per
  missing kWh. Prices the insurance value of a full battery against
  unforecast spikes and surprise load, which the pure forecast economics
  assign zero worth — on mild days the optimizer would otherwise stop at
  "enough for the forecast". Binds at an instant, not a floor: the battery
  still discharges freely into the evening peak.

## 0.1.8

- **Below-reserve SoC is no longer clamped up to `soc_min`**: the plan starts
  from the actual SoC (phantom energy was invented when a BMS recalibration
  or overnight self-consumption load left the battery under the reserve),
  never discharges below the real level, and recovers above the reserve when
  prices favor charging. DOCS now spells out `soc_min` as HEM's planning
  reserve vs the inverter's own minimum.

## 0.1.7

- Dashboard: "Amber buy / sell" tile — the live prices the current action was
  optimized against, with the 5-minute interval they apply to (#1).
- Dashboard: hover tooltip on the Horizon cost tile explaining what the
  number is (net meter cash flow over the horizon; excludes wear and
  terminal stored value); DOCS sensor table clarified to match.

## 0.1.6

- Dashboard: a load-forecast info line under the header — how many days of
  history the daily learn used, from which sensor and source (long-term
  statistics vs recorder history), hour-bucket coverage, and the fitted
  temperature response (sensor + peak kW/°C heating/cooling).
- **`load_forecast.history_days` option removed**: learning now always reads
  up to 365 days and self-caps to the history that actually exists — more
  data is strictly better, so there was nothing to configure. If the add-on
  complains about an unknown option after updating, remove the
  `load_forecast:` section from its Configuration (⋮ → Edit in YAML).
- **Backtesting removed** (`hem.backtest`, the `/data/history` JSONL recorder,
  and the `HEM_DATA_DIR` env var): the project is validated by reviewing the
  dry-run dashboard and monitoring live behaviour instead of programmatic
  replay. The add-on no longer writes anything to `/data` except its options.

## 0.1.5

- **`sensor.hem_plan` removed**: nothing consumed it (the dashboard reads the
  plan from the add-on directly) and its large attribute churned the recorder
  every 5 minutes. If you added a `recorder: exclude:` for it, you can drop
  that; the entity disappears on your next HA restart.
- Dashboard: the mode strip, SoC chart, and line charts now share one y-axis
  gutter and the exact plan time-span, so all charts align column-for-column.
  The SoC right-hand % axis is gone (it forced the plot out of alignment) —
  the tooltip shows kWh and % instead. Mode-strip tooltip follows the cursor.

## 0.1.4

- **`hold` replaced by `no_charge`**: the earlier `hold` action froze the
  battery (forced mode + stop), which wrongly imports instead of covering a
  load dip while deferring a charge. `no_charge` is self-consumption with
  charging blocked (Sungrow: max charge power 0), so the battery still serves
  the house. Blueprint gains `no_charge_actions` and a `restore_actions`
  sequence (max charge power back to full, run before every branch so the
  cap can't stick). The reverse case (block discharge to hold the reserve) is
  deferred to a future `no_discharge` action.
- Dashboard: setpoint tile shows "—" for every non-forced mode; the mode
  timeline gains a `no_charge` colour.


## 0.1.3

- Dashboard: new "Planned mode" timeline strip — the horizon colored by
  action (charge/discharge/hold/curtail/idle) at a glance.
- Dashboard: the setpoint tile shows "—" during idle/curtail (the battery is
  under self-consumption control; there is no commanded setpoint).
- Blueprint: the grid-connection input is a single binary sensor now
  (was a list) — re-select your sensor after re-importing.

## 0.1.2

- **New `hold` action**: the battery stays deliberately inactive while PV
  surplus exports (deferring the charge to a lower-value window) or load
  imports (saving stored energy for a better price) — jobs self-consumption
  mode cannot do. Blueprint gains an optional `hold_actions` input (Sungrow:
  forced mode + Stop); left empty, hold behaves as idle.
- Blueprint: optional grid-connection sensor(s) — any reading OFF reverts to
  idle/self-consumption immediately and re-asserts every 5 minutes.
- Price-event debounce reduced 10s -> 2s: HEM re-solves ~3s after a
  significant Amber price lands.

## 0.1.1

- **Grid-coupled action semantics**: `charge`/`discharge` are now reserved for
  moves your inverter's self-consumption mode would never make on its own —
  `charge` means charging from the grid, `discharge` means exporting stored
  energy. Running the house off the battery and charging from PV surplus both
  publish `idle`, so the actuator leaves the inverter in load-following
  self-consumption instead of pinning a forced setpoint.
- Blueprint: optional `curtail_actions`/`uncurtail_actions` inputs for
  negative feed-in export capping, with the un-cap wired into every branch
  including the failsafe.
- Publisher: `sensor.hem_action` carries `power_kw`/`power_w` attributes
  (atomic with the action); the blueprint reads power from there.
- Solver-failure fallback (reuse the previous plan shifted forward) now
  actually runs in production.
- Load learner: per-day bidirectional unit-mislabel correction, local-hour
  splitting of statistics rows (removes a ~30-min profile lag), bounded daily
  learn with proper retry backoff.
- First price/spike change after a restart triggers an early re-solve.

## 0.1.0

- Initial release: rolling-horizon MILP battery optimizer for Amber Electric
  5-minute pricing, learned load forecasting with temperature response,
  spike-reserve hedging, dry-run recommendation sensors, ingress dashboard,
  actuator blueprint with heartbeat failsafe, receding-horizon backtester.

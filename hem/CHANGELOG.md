# Changelog

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

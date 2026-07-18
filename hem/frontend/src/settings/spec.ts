// The Settings form, described as data: sections -> fields, with inline help
// ported from DOCS.md (which stays as the deep-dive; this is the canonical
// in-app field documentation). The server's pydantic Settings model is the
// validation authority — min/max here are input hints only.

export type FieldKind = "entity" | "number" | "boolean" | "select" | "text" | "time";

export interface FieldSpec {
  path: string; // dot path in the config document, e.g. "battery.capacity_kwh"
  label: string;
  kind: FieldKind;
  help: string;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  /** No server-side default — must be filled before the form can save. */
  required?: boolean;
  /** Default shown for fresh installs (mirrors the pydantic default). */
  default?: string | boolean;
  options?: { value: string; label: string }[];
  /** Entity picker filter: include only these domains. */
  domains?: string[];
  /** Entity picker: empty means "not used" instead of invalid. */
  optional?: boolean;
}

export interface SectionSpec {
  id: string;
  title: string;
  description: string;
  fields: FieldSpec[];
}

const entity = (
  path: string,
  label: string,
  help: string,
  domains: string[],
  opts: Partial<FieldSpec> = {},
): FieldSpec => ({ path, label, help, domains, kind: "entity", required: !opts.optional, ...opts });

const number = (
  path: string,
  label: string,
  help: string,
  opts: Partial<FieldSpec> = {},
): FieldSpec => ({ path, label, help, kind: "number", ...opts });

export const SECTIONS: SectionSpec[] = [
  {
    id: "entities",
    title: "Entities",
    description:
      "Point HEM at your Home Assistant entities. Amber Express's forecast " +
      "attributes live on the price sensors themselves, so the two price " +
      "entities cover both live prices and forecasts.",
    fields: [
      entity("entities.buy_price", "Buy price", "Amber Express general price sensor ($/kWh).", [
        "sensor",
      ]),
      entity(
        "entities.sell_price",
        "Feed-in price",
        "Amber Express feed-in price sensor ($/kWh).",
        ["sensor"],
      ),
      entity(
        "entities.price_spike",
        "Price spike",
        "Amber Express price-spike binary sensor — enables the spike reserve strategy.",
        ["binary_sensor"],
        { optional: true, default: "" },
      ),
      entity(
        "entities.pv_forecast_today",
        "PV forecast (today)",
        "Open-Meteo Solar Forecast energy production today.",
        ["sensor"],
      ),
      entity(
        "entities.pv_forecast_tomorrow",
        "PV forecast (tomorrow)",
        "Open-Meteo Solar Forecast energy production tomorrow.",
        ["sensor"],
      ),
      entity("entities.battery_soc", "Battery SoC", "Battery level sensor (%).", ["sensor"]),
      entity(
        "entities.battery_power",
        "Battery power",
        "Battery power sensor (W or kW). Set the sign convention under Battery.",
        ["sensor"],
      ),
      entity(
        "entities.weather",
        "Weather",
        "Any weather entity with an hourly forecast — feeds the temperature response.",
        ["weather"],
      ),
      entity(
        "entities.load_power",
        "House load power",
        "House load sensor (W or kW), e.g. the mkaiser package's load_power. The load " +
          "forecast is learned from its history. Strongly recommended: without it HEM " +
          "plans with ZERO house load.",
        ["sensor"],
        { optional: true, default: "" },
      ),
      entity(
        "entities.outdoor_temp",
        "Outdoor temperature",
        "Outdoor temperature sensor with long-term statistics — enables the learned " +
          "temperature response (extra kW per degree of cooling/heating).",
        ["sensor"],
        { optional: true, default: "" },
      ),
    ],
  },
  {
    id: "battery",
    title: "Battery",
    description:
      "Physical parameters of the battery and how its wear and reserves are priced.",
    fields: [
      number("battery.capacity_kwh", "Capacity", "Usable battery capacity.", {
        unit: "kWh",
        min: 0.5,
        step: 0.1,
        required: true,
      }),
      number("battery.max_charge_kw", "Max charge power", "Battery-side charging limit.", {
        unit: "kW",
        min: 0.1,
        step: 0.1,
        required: true,
      }),
      number(
        "battery.max_discharge_kw",
        "Max discharge power",
        "Battery-side everyday discharge limit (wear-conscious — the spike section can " +
          "temporarily raise it).",
        { unit: "kW", min: 0.1, step: 0.1, required: true },
      ),
      number("battery.efficiency_charge", "Charge efficiency", "AC→DC charge efficiency.", {
        min: 0.5,
        max: 1,
        step: 0.01,
        default: "0.95",
      }),
      number(
        "battery.efficiency_discharge",
        "Discharge efficiency",
        "DC→AC discharge efficiency.",
        { min: 0.5, max: 1, step: 0.01, default: "0.95" },
      ),
      number(
        "battery.soc_min",
        "Planning reserve (SoC min)",
        "HEM's planning reserve, NOT the inverter's minimum SoC — set it above the " +
          "inverter's own floor as insurance against forecast error. Deliberate " +
          "discharges stop here; idle self-consumption can still drain below it, which " +
          "is what the reserve insures against.",
        { min: 0, max: 1, step: 0.01, default: "0.1" },
      ),
      number("battery.soc_max", "SoC max", "Upper SoC bound as a fraction of capacity.", {
        min: 0,
        max: 1,
        step: 0.01,
        default: "1",
      }),
      number(
        "battery.wear_cost_per_kwh",
        "Wear cost",
        "Degradation cost charged against every discharged kWh — e.g. replacement cost " +
          "divided by lifetime throughput ($6000 / 38 MWh ≈ $0.16, or much lower if the " +
          "battery will outlive its warranty).",
        { unit: "$/kWh", min: 0, step: 0.01, default: "0.04" },
      ),
      {
        path: "battery.allow_grid_charge",
        label: "Allow grid charging",
        kind: "boolean",
        default: true,
        help: "Permit charging the battery from the grid (not just PV surplus).",
      },
      {
        path: "battery.power_convention",
        label: "Power sign convention",
        kind: "select",
        default: "charge_negative",
        options: [
          { value: "charge_negative", label: "positive = discharging (mkaiser Sungrow)" },
          { value: "charge_positive", label: "positive = charging" },
        ],
        help: "Which sign your battery power sensor reports while charging.",
      },
      number(
        "battery.daily_target_soc",
        "Daily full-charge target",
        "Daily insurance target SoC (fraction of capacity; 0 disables). A rational " +
          "optimizer only charges enough for the forecast — this softly requires the " +
          "battery at the target by the hour below, so unforecast spikes and surprise " +
          "usage find it charged. Binds at an instant, not a floor: the battery still " +
          "discharges freely into the evening peak.",
        { min: 0, max: 1, step: 0.05, default: "0" },
      ),
      {
        path: "battery.daily_target_time",
        label: "Daily target time",
        kind: "time",
        default: "15:00",
        help: "Local time of day the daily target applies at (default 15:00, before the evening ramp).",
      },
      number(
        "battery.daily_target_penalty_per_kwh",
        "Daily target penalty",
        "Maximum willingness-to-pay per missing kWh at the target — anything cheaper " +
          "WILL be bought. Set it between your typical feed-in price and your typical " +
          "grid buy price (e.g. $0.10 with ~$0.08 feed-in and ~$0.25 grid); above the " +
          "grid price the planner will import at full price to hit the target.",
        { unit: "$/kWh", min: 0, max: 10, step: 0.01, default: "0.1" },
      ),
    ],
  },
  {
    id: "grid",
    title: "Grid connection",
    description:
      "Limits of the grid connection at the meter — distinct from the battery's own " +
      "power limits; the optimizer respects both simultaneously.",
    fields: [
      number(
        "grid.import_limit_kw",
        "Import limit",
        "Maximum net draw from the grid for the whole house (connection/main-breaker capacity).",
        { unit: "kW", min: 0.1, step: 0.5, required: true },
      ),
      number(
        "grid.export_limit_kw",
        "Export limit",
        "Maximum net feed-in allowed by your DNSP/connection agreement. Caps what " +
          "reaches the grid regardless of battery discharge (export = battery + PV − load). " +
          "If you raise the spike discharge cap, raise this to match or the extra power " +
          "has nowhere to go.",
        { unit: "kW", min: 0, step: 0.5, required: true },
      ),
    ],
  },
  {
    id: "optimizer",
    title: "Optimizer",
    description: "Horizon, solver limits and behavioral thresholds.",
    fields: [
      number(
        "optimizer.horizon_hours",
        "Horizon",
        "How far ahead each plan looks. Longer sees more of tomorrow's solar; beyond " +
          "the price forecast the tail is padded (shaded on the dashboard).",
        { unit: "h", min: 2, max: 72, step: 1, default: "36" },
      ),
      {
        path: "optimizer.terminal_soc_value",
        label: "Terminal SoC value",
        kind: "text",
        default: "auto",
        help:
          "How leftover stored energy is valued at the horizon end, in $/kWh (NOT a " +
          "target SoC). \"auto\" = median buy price × discharge efficiency − wear cost. " +
          "Without it the optimizer would dump the battery at any positive price before " +
          "the horizon. Enter \"auto\" or a number.",
      },
      number(
        "optimizer.solver_timeout_s",
        "Solver timeout",
        "HiGHS time limit per solve; normal solves take tens of milliseconds.",
        { unit: "s", min: 1, max: 60, step: 1, default: "30" },
      ),
      number(
        "optimizer.action_switch_threshold_dollars",
        "Action switch threshold",
        "Hysteresis: the current action only changes if switching improves the horizon " +
          "objective by more than this.",
        { unit: "$", min: 0, max: 10, step: 0.01, default: "0.02" },
      ),
      number(
        "optimizer.forecast_haircut",
        "Forecast haircut",
        "Fraction of the above-median excess shaved off sell prices more than 6h out, " +
          "so distant phantom spikes don't distort near-term decisions. The spike " +
          "reserve reads raw prices, unaffected.",
        { min: 0, max: 1, step: 0.05, default: "0.2" },
      ),
    ],
  },
  {
    id: "spike",
    title: "Spike strategy",
    description:
      "When Amber flags a potential price spike within the lookahead, HEM softly " +
      "reserves energy in the battery so it can sell into the spike if it confirms.",
    fields: [
      number("spike.lookahead_hours", "Lookahead", "How far ahead to honor potential spikes.", {
        unit: "h",
        min: 0,
        max: 48,
        step: 0.5,
        default: "4",
      }),
      number("spike.reserve_kwh", "Reserve", "Energy kept in the battery while a spike looms.", {
        unit: "kWh",
        min: 0,
        step: 0.5,
        default: "6",
      }),
      number(
        "spike.high_price_threshold",
        "High price threshold",
        "Forecast sell price that counts as spike-worthy even without an Amber flag.",
        { unit: "$/kWh", min: 0, max: 20, step: 0.1, default: "1" },
      ),
      number(
        "spike.reserve_penalty_per_kwh",
        "Reserve penalty",
        "Softness of the reserve: cost per kWh per hour spent below it. Below true " +
          "spike value but above normal arbitrage margin, so only genuinely better " +
          "opportunities break the reserve.",
        { unit: "$/kWh", min: 0, max: 20, step: 0.1, default: "0.5" },
      ),
      number(
        "spike.discharge_kw",
        "Spike discharge cap",
        "Discharge limit while a CONFIRMED spike is active (current interval only) — " +
          "lets a wear-conscious everyday limit be exceeded for the rare high-value " +
          "hours. Set to your inverter's true limit; 0 disables. Extra power only " +
          "reaches the grid if the export limit allows it.",
        { unit: "kW", min: 0, max: 100, step: 0.5, default: "0" },
      ),
    ],
  },
];

/** Flat list of every field spec, for lookups by path. */
export const ALL_FIELDS: FieldSpec[] = SECTIONS.flatMap((s) => s.fields);

export function getPath(doc: Record<string, unknown> | null, path: string): unknown {
  let node: unknown = doc;
  for (const key of path.split(".")) {
    if (node == null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[key];
  }
  return node;
}

export function setPath(doc: Record<string, unknown>, path: string, value: unknown): void {
  const keys = path.split(".");
  let node = doc;
  for (const key of keys.slice(0, -1)) {
    node = (node[key] ??= {}) as Record<string, unknown>;
  }
  node[keys[keys.length - 1]!] = value;
}

// Typed mirror of the add-on's /api/plan payload (see hem/web/app.py).
// The API is unchanged by the React migration — this file is the contract.

export type Action = "charge" | "discharge" | "idle" | "no_charge" | "curtail";

export interface PlanInterval {
  start: string; // ISO datetime
  end: string;
  action: Action;
  power_kw: number; // +charge / −discharge
  soc_start: number; // kWh
  soc_end: number;
  buy: number; // $/kWh
  sell: number;
  pv_kw: number;
  load_kw: number;
  grid_import_kw: number;
  grid_export_kw: number;
  interval_cost: number;
}

export interface LoadForecastInfo {
  load_entity?: string;
  source?: string;
  window_days?: number;
  hours_used?: number;
  buckets?: string;
  temp_response?: boolean;
  learned_at?: string;
  temp_entity?: string;
  heat_kw_per_deg?: number;
  cool_kw_per_deg?: number;
}

// Some fields are not consumed by the UI (yet) — this file mirrors the FULL
// payload so the contract is visible in one place.
export interface PlanMeta {
  capacity_kwh?: number;
  price_forecast_end?: string | null;
  coverage?: Record<string, number> | null;
  load_forecast?: "learned" | "pending" | "unconfigured";
  load_forecast_info?: LoadForecastInfo;
}

export interface PlanResponse {
  computed_at: string;
  solver_status: string;
  solve_ms: number;
  objective_cost: number;
  meta: PlanMeta;
  intervals: PlanInterval[];
}

export class PlanError extends Error {}

export async function fetchPlan(): Promise<PlanResponse> {
  // Relative URL: the page lives behind HA ingress at an unpredictable prefix.
  const resp = await fetch("./api/plan", { cache: "no-store" });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = ((await resp.json()) as { error?: string }).error ?? detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new PlanError(detail);
  }
  return (await resp.json()) as PlanResponse;
}

export async function fetchHealthError(): Promise<string> {
  try {
    const resp = await fetch("./health", { cache: "no-store" });
    const body = (await resp.json()) as { last_error?: string };
    return body.last_error ?? "";
  } catch {
    return "";
  }
}

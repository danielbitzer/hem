// Zod mirror of the add-on's /api/plan payload (see hem/web/app.py) — the
// single source of truth for the contract: TS types are inferred from the
// schemas, and every response is validated so a drift fails loudly at the
// fetch boundary instead of rendering garbage. The API is unchanged by the
// React migration. Frontend and backend ship in the same image, so strict
// validation carries no version-skew risk.

import { z } from "zod";

export const ActionSchema = z.enum(["charge", "discharge", "idle", "no_charge", "curtail"]);
export type Action = z.infer<typeof ActionSchema>;

export const PlanIntervalSchema = z.object({
  start: z.string(), // ISO datetime
  end: z.string(),
  action: ActionSchema,
  power_kw: z.number(), // +charge / −discharge
  soc_start: z.number(), // kWh
  soc_end: z.number(),
  buy: z.number(), // $/kWh
  sell: z.number(),
  pv_kw: z.number(),
  load_kw: z.number(),
  grid_import_kw: z.number(),
  grid_export_kw: z.number(),
  interval_cost: z.number(),
});
export type PlanInterval = z.infer<typeof PlanIntervalSchema>;

export const LoadForecastInfoSchema = z.looseObject({
  load_entity: z.string().optional(),
  source: z.string().optional(),
  window_days: z.number().optional(),
  hours_used: z.number().optional(),
  buckets: z.string().optional(),
  temp_response: z.boolean().optional(),
  learned_at: z.string().optional(),
  temp_entity: z.string().optional(),
  heat_kw_per_deg: z.number().optional(),
  cool_kw_per_deg: z.number().optional(),
});
export type LoadForecastInfo = z.infer<typeof LoadForecastInfoSchema>;

// meta is a loose object: the backend adds informational keys over time and
// the UI should keep working (and keep validating) without listing them all.
export const PlanMetaSchema = z.looseObject({
  capacity_kwh: z.number().optional(),
  price_forecast_end: z.string().nullish(),
  coverage: z.record(z.string(), z.number()).nullish(),
  load_forecast: z.enum(["learned", "pending", "unconfigured"]).optional(),
  load_forecast_info: LoadForecastInfoSchema.optional(),
});
export type PlanMeta = z.infer<typeof PlanMetaSchema>;

export const PlanResponseSchema = z.object({
  computed_at: z.string(),
  solver_status: z.string(),
  solve_ms: z.number(),
  objective_cost: z.number(),
  meta: PlanMetaSchema,
  intervals: z.array(PlanIntervalSchema),
});
export type PlanResponse = z.infer<typeof PlanResponseSchema>;

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
  const parsed = PlanResponseSchema.safeParse(await resp.json());
  if (!parsed.success) {
    throw new PlanError(`plan payload failed validation: ${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
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

/** fetchPlan with the add-on's last cycle error appended — the message the
 * dashboard shows when a poll fails. Used as the TanStack Query queryFn. */
export async function fetchPlanOrExplain(): Promise<PlanResponse> {
  try {
    return await fetchPlan();
  } catch (e) {
    let msg = `No plan yet: ${e instanceof PlanError ? e.message : String(e)}`;
    const lastError = await fetchHealthError();
    if (lastError) msg += ` — last cycle error: ${lastError}`;
    throw new PlanError(msg);
  }
}

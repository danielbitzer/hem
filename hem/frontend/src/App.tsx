import { useEffect, useMemo, useState } from "react";
import { fetchHealthError, fetchPlan, PlanError, type PlanResponse } from "./api";
import { BatteryChart, ForecastChart, PricesChart, type Row, SocChart } from "./charts";
import { ModeStrip } from "./ModeStrip";
import { Tiles } from "./Tiles";

const REFRESH_MS = 60_000;

function metaLine(plan: PlanResponse, tEnd: number): string {
  const computed = new Date(plan.computed_at).toLocaleString();
  let text = `computed ${computed} · ${plan.solver_status} · ${Math.round(plan.solve_ms)} ms · ${plan.intervals.length} intervals`;
  const fcEnd = plan.meta.price_forecast_end;
  if (fcEnd && Date.parse(fcEnd) < tEnd) {
    text += ` · price forecast ends ${new Date(fcEnd).toLocaleString()} (tail is held flat)`;
  }
  return text;
}

function loadForecastLine(plan: PlanResponse): string | null {
  const lf = plan.meta.load_forecast_info;
  if (plan.meta.load_forecast !== "learned" || !lf?.window_days) return null;
  const days = Math.max(1, Math.round(lf.window_days));
  let text =
    `load forecast: learned from ${days} day${days === 1 ? "" : "s"} of ` +
    `${lf.source} (${lf.load_entity}) · ${lf.buckets} hour buckets`;
  text += lf.temp_response
    ? ` · temperature response from ${lf.temp_entity} — up to ` +
      `${lf.heat_kw_per_deg} kW/°C heating, ${lf.cool_kw_per_deg} kW/°C cooling`
    : " · no temperature response";
  return text;
}

function warningText(plan: PlanResponse): string | null {
  const status = plan.meta.load_forecast;
  if (!status || status === "learned") return null;
  return status === "unconfigured"
    ? "⚠ Load forecasting unavailable — no house load sensor configured (entities.load_power). " +
        "Plans assume ZERO house load; consider raising battery.soc_min until learning is set up."
    : "⚠ Load forecast not learned yet — waiting for usable history from the load sensor. " +
        "Plans assume ZERO house load; consider raising battery.soc_min until learning kicks in.";
}

export function App() {
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const next = await fetchPlan();
        if (!alive) return;
        // Keep the previous object identity for identical plans so the memoized
        // rows — and every chart under them — skip re-rendering on quiet polls.
        setPlan((prev) => (prev && prev.computed_at === next.computed_at ? prev : next));
        setError(null);
      } catch (e) {
        if (!alive) return;
        let msg = `No plan yet: ${e instanceof PlanError ? e.message : String(e)}`;
        const lastError = await fetchHealthError();
        if (lastError) msg += ` — last cycle error: ${lastError}`;
        if (alive) setError(msg);
      }
    };
    void refresh();
    const id = setInterval(() => void refresh(), REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Parse interval timestamps exactly once; every child works from Row.
  const rows = useMemo<Row[]>(
    () =>
      (plan?.intervals ?? []).map((iv) => ({
        t: Date.parse(iv.start),
        end: Date.parse(iv.end),
        action: iv.action,
        buy: iv.buy,
        sell: iv.sell,
        pv: iv.pv_kw,
        load: iv.load_kw,
        battery: iv.power_kw,
        gridImport: iv.grid_import_kw,
        gridExport: -iv.grid_export_kw,
        soc: iv.soc_end,
      })),
    [plan],
  );

  // Step charts need a closing point at the final interval's END, or every
  // line stops one interval short of the axis edge (and of the mode strip).
  const chartRows = useMemo<Row[]>(() => {
    const last = rows[rows.length - 1];
    return last ? [...rows, { ...last, t: last.end }] : rows;
  }, [rows]);

  if (!plan) {
    return (
      <div className="p-6 text-center">
        {error ? <span className="text-[#c0392b]">{error}</span> : "loading…"}
      </div>
    );
  }

  const first = rows[0];
  const last = rows[rows.length - 1];
  if (!first || !last) {
    return <div className="p-6 text-center">plan is empty — waiting for the next cycle</div>;
  }
  const domain: [number, number] = [first.t, last.end];
  const fcEnd = plan.meta.price_forecast_end ? Date.parse(plan.meta.price_forecast_end) : null;
  const loadLine = loadForecastLine(plan);
  const warning = warningText(plan);

  return (
    <div>
      <header className="mb-4">
        <h1 className="mb-1 text-lg font-bold">Home Energy Manager</h1>
        <div className="text-xs text-muted">{metaLine(plan, domain[1])}</div>
        {loadLine && <div className="mt-1 text-xs text-muted">{loadLine}</div>}
        {error && <div className="mt-1 text-xs text-[#c0392b]">{error}</div>}
      </header>
      {warning && (
        <div className="mb-3.5 rounded-xl border border-[#e67e22] bg-[#e67e22]/10 px-3.5 py-2.5 text-[13px]">
          {warning}
        </div>
      )}
      <Tiles plan={plan} rows={rows} />
      <PricesChart rows={chartRows} domain={domain} forecastEnd={fcEnd} />
      <ForecastChart rows={chartRows} domain={domain} />
      <ModeStrip rows={rows} domain={domain} />
      <BatteryChart rows={chartRows} domain={domain} />
      <SocChart rows={chartRows} domain={domain} capacity={plan.meta.capacity_kwh ?? null} />
    </div>
  );
}

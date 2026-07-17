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
  let text =
    `load forecast: learned from ${Math.round(lf.window_days)} days of ` +
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
        setPlan(next);
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

  const rows = useMemo<Row[]>(
    () =>
      (plan?.intervals ?? []).map((iv) => ({
        t: Date.parse(iv.start),
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

  if (!plan) {
    return (
      <div className="p-6 text-center">
        {error ? <span className="text-[#c0392b]">{error}</span> : "loading…"}
      </div>
    );
  }

  const first = rows[0];
  const lastIv = plan.intervals[plan.intervals.length - 1];
  if (!first || !lastIv) return null;
  const domain: [number, number] = [first.t, Date.parse(lastIv.end)];
  const fcEnd = plan.meta.price_forecast_end ? Date.parse(plan.meta.price_forecast_end) : null;
  const loadLine = loadForecastLine(plan);
  const warning = warningText(plan);

  return (
    <div>
      <h1 className="mb-1 text-lg font-bold">Home Energy Manager</h1>
      <div className="mb-1 text-xs text-muted">{metaLine(plan, domain[1])}</div>
      {loadLine && <div className="mb-1 text-xs text-muted">{loadLine}</div>}
      {error && <div className="mb-2 text-xs text-[#c0392b]">{error}</div>}
      <div className="h-2" />
      {warning && (
        <div className="mb-3.5 rounded-xl border border-[#e67e22] bg-[#e67e22]/10 px-3.5 py-2.5 text-[13px]">
          {warning}
        </div>
      )}
      <Tiles plan={plan} />
      <PricesChart rows={rows} domain={domain} forecastEnd={fcEnd} />
      <ForecastChart rows={rows} domain={domain} />
      <ModeStrip intervals={plan.intervals} domain={domain} />
      <BatteryChart rows={rows} domain={domain} />
      <SocChart rows={rows} domain={domain} capacity={plan.meta.capacity_kwh ?? null} />
    </div>
  );
}

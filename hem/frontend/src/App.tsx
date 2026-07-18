import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { type ConfigResponse, fetchConfig, fetchPlanOrExplain, type PlanResponse } from "./api";
import { BatteryChart, ForecastChart, PricesChart, type Row, SocChart } from "./charts";
import { Button } from "./components/ui/button";
import { ModeStrip } from "./ModeStrip";
import { SettingsView } from "./settings/SettingsView";
import { Tiles } from "./Tiles";

const REFRESH_MS = 60_000;

type View = "dashboard" | "settings";

export function App() {
  const [chosenView, setChosenView] = useState<View | null>(null);
  // Polled so the lifecycle banner clears when the main loop flips to
  // running shortly after an enable (the save-triggered refetch can race it).
  const config = useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
    refetchInterval: 30_000,
  });
  // A fresh install lands (and STAYS — hence pinning it as the chosen view,
  // or the first successful save would yank the user to the dashboard) in
  // Settings; once the user navigates, their choice wins.
  useEffect(() => {
    if (chosenView === null && config.data && !config.data.configured) {
      setChosenView("settings");
    }
  }, [chosenView, config.data]);
  const view: View = chosenView ?? "dashboard";

  return (
    <div>
      <header className="mb-4 flex items-start justify-between gap-4">
        <h1 className="text-lg font-bold">Home Energy Manager</h1>
        <nav className="flex gap-1.5">
          {(["dashboard", "settings"] as const).map((v) => (
            <Button
              key={v}
              size="sm"
              variant={view === v ? "default" : "ghost"}
              aria-current={view === v ? "page" : undefined}
              onClick={() => setChosenView(v)}
            >
              {v === "dashboard" ? "Dashboard" : "Settings"}
            </Button>
          ))}
        </nav>
      </header>
      {view === "settings" ? <SettingsView /> : <Dashboard config={config.data} />}
    </div>
  );
}

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

function lifecycleBanner(config: ConfigResponse | undefined): string | null {
  if (!config || config.lifecycle === "running") return null;
  return config.lifecycle === "unconfigured"
    ? "HEM is not configured yet — no planning cycles run. Open Settings to configure and enable it."
    : "HEM is disabled — no planning cycles run and your actuator's failsafe keeps the inverter " +
        "in self-consumption. Enable it in Settings.";
}

function Dashboard({ config }: { config: ConfigResponse | undefined }) {
  // On error the last good plan stays rendered with the error line above it.
  // Structural sharing keeps object identity for unchanged payloads, so quiet
  // polls don't re-render the charts. (No useMemo below: the React Compiler
  // memoizes these derivations.)
  const { data: plan, error: queryError } = useQuery<PlanResponse>({
    queryKey: ["plan"],
    queryFn: fetchPlanOrExplain,
    refetchInterval: REFRESH_MS,
    retry: false,
  });
  const error = queryError ? queryError.message : null;
  const banner = lifecycleBanner(config);

  // Parse interval timestamps exactly once; every child works from Row.
  const rows: Row[] = (plan?.intervals ?? []).map((iv) => ({
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
  }));

  // Step charts need a closing point at the final interval's END, or every
  // line stops one interval short of the axis edge (and of the mode strip).
  const lastRow = rows[rows.length - 1];
  const chartRows: Row[] = lastRow ? [...rows, { ...lastRow, t: lastRow.end }] : rows;

  if (!plan) {
    return (
      <div>
        {banner && <Banner text={banner} />}
        <div className="p-6 text-center">
          {error ? <span className="text-[#c0392b]">{error}</span> : "loading…"}
        </div>
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
      <div className="mb-4">
        <div className="text-muted-foreground text-xs">{metaLine(plan, domain[1])}</div>
        {loadLine && <div className="text-muted-foreground mt-1 text-xs">{loadLine}</div>}
        {error && <div className="mt-1 text-xs text-[#c0392b]">{error}</div>}
      </div>
      {banner && <Banner text={banner} />}
      {warning && <Banner text={warning} />}
      <Tiles plan={plan} rows={rows} />
      <PricesChart rows={chartRows} domain={domain} forecastEnd={fcEnd} />
      <ForecastChart rows={chartRows} domain={domain} />
      <ModeStrip rows={rows} domain={domain} />
      <BatteryChart rows={chartRows} domain={domain} />
      <SocChart rows={chartRows} domain={domain} capacity={plan.meta.capacity_kwh ?? null} />
    </div>
  );
}

function Banner({ text }: { text: string }) {
  return (
    <div className="mb-3.5 rounded-xl border border-[#e67e22] bg-[#e67e22]/10 px-3.5 py-2.5 text-[13px]">
      {text}
    </div>
  );
}

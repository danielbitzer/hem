import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { type ConfigResponse, fetchConfig, fetchPlanOrExplain, type PlanResponse } from "./api";
import { BatteryChart, ForecastChart, PricesChart, type Row, SocChart } from "./charts";
import { ModeStrip } from "./ModeStrip";
import { SettingsView } from "./settings/SettingsView";
import { Hero, Stats } from "./Tiles";
import { fmtTime } from "./theme";

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
  const plan = useQuery<PlanResponse>({
    queryKey: ["plan"],
    queryFn: fetchPlanOrExplain,
    refetchInterval: REFRESH_MS,
    retry: false,
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
    <div className="min-h-screen">
      {/* Full-bleed header bar; its content aligns with the capped main column */}
      <header className="border-b border-border bg-card px-[22px] py-[18px]">
        <div className="mx-auto flex max-w-[960px] flex-wrap items-start justify-between gap-x-4 gap-y-2.5">
          <div className="min-w-0 max-sm:w-full">
            <h1 className="text-[17px] font-bold text-foreground">Home Energy Manager</h1>
            {plan.data && (
              // Truncates to one line on desktop; wraps on phones (where the
              // full-width header has room) instead of clipping to "79 interv…".
              <div className="mt-1 min-w-0 font-mono text-xs text-muted-foreground sm:truncate">
                {metaLine(plan.data)}
              </div>
            )}
            {plan.error && (
              <div className="mt-1 text-xs text-destructive">{plan.error.message}</div>
            )}
          </div>
          {/* Full-width segmented toggle on phones; compact pill on desktop.
              The track is a dark inset (bg-tab-bg) and the active tab a raised
              surface so the two read as distinct from the header bar. */}
          <nav className="flex shrink-0 gap-[3px] rounded-full bg-tab-bg p-[3px] max-sm:w-full">
            {(["dashboard", "settings"] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-current={view === v ? "page" : undefined}
                onClick={() => setChosenView(v)}
                className={
                  "cursor-pointer rounded-full border-none px-4 py-[7px] text-[13px] font-semibold transition-all max-sm:flex-1 max-sm:py-2 " +
                  (view === v
                    ? "bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,.12)] dark:bg-[#2c2c2c] dark:shadow-none"
                    : "bg-transparent text-muted-foreground hover:text-foreground")
                }
              >
                {v === "dashboard" ? "Dashboard" : "Settings"}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto flex max-w-[960px] flex-col gap-3.5 p-5">
        {view === "settings" ? (
          <SettingsView />
        ) : (
          <Dashboard config={config.data} plan={plan.data} />
        )}
      </main>
    </div>
  );
}

function metaLine(plan: PlanResponse): string {
  const last = plan.intervals[plan.intervals.length - 1];
  const horizonH =
    plan.intervals.length && last
      ? Math.round((Date.parse(last.end) - Date.parse(plan.intervals[0]!.start)) / 3_600_000)
      : 0;
  return (
    `computed ${fmtTime(Date.parse(plan.computed_at))} · ${plan.solver_status} · ` +
    `${Math.round(plan.solve_ms)} ms · ${plan.intervals.length} intervals · horizon ${horizonH} h`
  );
}

function loadForecastLine(plan: PlanResponse): string | null {
  const lf = plan.meta.load_forecast_info;
  if (plan.meta.load_forecast !== "learned" || !lf?.window_days) return null;
  const days = Math.max(1, Math.round(lf.window_days));
  let text =
    `load forecast: ${days} day${days === 1 ? "" : "s"} of ${lf.source} (${lf.load_entity})`;
  text += lf.temp_response
    ? ` · temp response ${lf.heat_kw_per_deg} kW/°C heat, ${lf.cool_kw_per_deg} kW/°C cool`
    : " · no temperature response";
  if (lf.buffer) text += ` · +${Math.round(lf.buffer * 100)}% buffer`;
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

function vacationBanner(plan: PlanResponse): string | null {
  const v = plan.meta.vacation;
  if (!v) return null;
  const until = v.until
    ? `until ${new Date(v.until).toLocaleString()}`
    : "until turned off in Settings";
  return `🌴 Vacation mode — load forecast flattened to a ${v.baseline_kw} kW baseline ${until}.`;
}

function lifecycleBanner(config: ConfigResponse | undefined): string | null {
  if (!config || config.lifecycle === "running") return null;
  return config.lifecycle === "unconfigured"
    ? "HEM is not configured yet — no planning cycles run. Open Settings to configure and enable it."
    : "HEM is disabled — no planning cycles run and your actuator's failsafe keeps the inverter " +
        "in self-consumption. Enable it in Settings.";
}

function Dashboard({
  config,
  plan,
}: {
  config: ConfigResponse | undefined;
  plan: PlanResponse | undefined;
}) {
  const banner = lifecycleBanner(config);

  // Parse interval timestamps exactly once; every child works from Row.
  // (No useMemo: the React Compiler memoizes these derivations; TanStack
  // Query's structural sharing keeps identity stable on quiet polls.)
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
        <div className="p-6 text-center text-muted-foreground">loading…</div>
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
  const warning = warningText(plan);
  const vacation = vacationBanner(plan);

  return (
    <>
      {banner && <Banner text={banner} />}
      {vacation && <Banner text={vacation} />}
      {warning && <Banner text={warning} />}
      <Hero rows={rows} />
      <Stats plan={plan} rows={rows} />
      <PricesChart rows={chartRows} domain={domain} forecastEnd={fcEnd} />
      <ForecastChart rows={chartRows} domain={domain} info={loadForecastLine(plan)} />
      <ModeStrip rows={rows} domain={domain} />
      <BatteryChart rows={chartRows} domain={domain} />
      <SocChart rows={chartRows} domain={domain} capacity={plan.meta.capacity_kwh ?? null} />
    </>
  );
}

function Banner({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-[#efa63c] bg-[#efa63c]/10 px-3.5 py-2.5 text-[13px]">
      {text}
    </div>
  );
}

import type { PlanResponse } from "./api";
import { ACTION_COLORS, fmtTime } from "./theme";

const HORIZON_COST_HELP =
  "Expected net cash flow at the meter over the plan horizon: planned grid " +
  "imports at forecast buy prices minus exports at forecast sell prices. " +
  "Negative = net earnings. Excludes battery wear cost and the value of " +
  "energy still stored at the horizon end, so a plan that ends with a full " +
  "battery looks 'worse' than one that sold everything.";

function Tile({
  label,
  value,
  sub,
  valueColor,
  help,
}: {
  label: string;
  value: string;
  sub?: string;
  valueColor?: string;
  help?: string;
}) {
  return (
    <div className="min-w-[130px] rounded-xl border border-edge bg-card px-4 py-2.5">
      <div className="text-[11px] tracking-wider text-muted uppercase">
        {label}
        {help && (
          <span
            title={help}
            className="ml-1.5 inline-block size-[13px] cursor-help rounded-full border border-muted text-center text-[9px] leading-3 normal-case"
          >
            ?
          </span>
        )}
      </div>
      <div className="mt-0.5 text-xl font-semibold" style={valueColor ? { color: valueColor } : undefined}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

export function Tiles({ plan }: { plan: PlanResponse }) {
  const step0 = plan.intervals[0];
  if (!step0) return null;
  const cap = plan.meta.capacity_kwh;
  const forced = step0.action === "charge" || step0.action === "discharge";

  const loadKwh = plan.intervals.reduce(
    (sum, iv) => sum + (iv.load_kw * (Date.parse(iv.end) - Date.parse(iv.start))) / 3_600_000,
    0,
  );
  const last = plan.intervals[plan.intervals.length - 1]!;
  const horizonH = (Date.parse(last.end) - Date.parse(step0.start)) / 3_600_000;

  return (
    <div className="mb-4 flex flex-wrap gap-3">
      <Tile
        label="Action now"
        value={step0.action.replace("_", " ")}
        valueColor={ACTION_COLORS[step0.action] ?? undefined}
      />
      <Tile
        label="Amber buy / sell"
        value={`$${step0.buy.toFixed(2)} / $${step0.sell.toFixed(2)}`}
        sub={`${fmtTime(Date.parse(step0.start))} – ${fmtTime(Date.parse(step0.end))}`}
      />
      <Tile label="Battery setpoint" value={forced ? `${step0.power_kw.toFixed(2)} kW` : "—"} />
      <Tile
        label="SoC target"
        value={
          cap
            ? `${((100 * step0.soc_end) / cap).toFixed(0)}% · ${step0.soc_end.toFixed(1)} kWh`
            : `${step0.soc_end.toFixed(1)} kWh`
        }
      />
      <Tile label="Horizon cost" value={`$${plan.objective_cost.toFixed(2)}`} help={HORIZON_COST_HELP} />
      <Tile label="Forecast load" value={`${loadKwh.toFixed(1)} kWh / ${Math.round(horizonH)}h`} />
    </div>
  );
}

import type { PlanResponse } from "./api";
import type { Row } from "./charts";
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
    <div className="min-w-[130px] rounded-xl border border-border bg-card px-4 py-2.5">
      <div className="text-[11px] tracking-wider text-muted-foreground uppercase">
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
      {sub && <div className="mt-0.5 text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function Tiles({ plan, rows }: { plan: PlanResponse; rows: Row[] }) {
  const step0 = rows[0];
  if (!step0) return null;
  const cap = plan.meta.capacity_kwh;
  const forced = step0.action === "charge" || step0.action === "discharge";

  const loadKwh = rows.reduce((sum, r) => sum + (r.load * (r.end - r.t)) / 3_600_000, 0);
  const last = rows[rows.length - 1]!;
  const horizonH = (last.end - step0.t) / 3_600_000;

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
        sub={`${fmtTime(step0.t)} – ${fmtTime(step0.end)}`}
      />
      <Tile label="Battery setpoint" value={forced ? `${step0.battery.toFixed(2)} kW` : "—"} />
      <Tile
        label="SoC target"
        value={
          cap
            ? `${((100 * step0.soc) / cap).toFixed(0)}% · ${step0.soc.toFixed(1)} kWh`
            : `${step0.soc.toFixed(1)} kWh`
        }
      />
      <Tile label="Horizon cost" value={`$${plan.objective_cost.toFixed(2)}`} help={HORIZON_COST_HELP} />
      <Tile label="Forecast load" value={`${loadKwh.toFixed(1)} kWh / ${Math.round(horizonH)}h`} />
    </div>
  );
}

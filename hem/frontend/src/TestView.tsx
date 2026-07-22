import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { fetchScenarios, runHistorySimulation, runSimulation, type SimOverrides } from "./api";
import { Button } from "./components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./components/ui/card";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import { PlanView } from "./PlanView";

const numOrNull = (s: string): number | null => {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isNaN(n) ? null : n;
};

/** Yesterday at this time, as a datetime-local value (browser-local). */
function defaultAt(): string {
  const d = new Date(Date.now() - 24 * 3600 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

type Mode = "scenario" | "history";
const MODES: { id: Mode; label: string }[] = [
  { id: "scenario", label: "Scenarios" },
  { id: "history", label: "Time travel" },
];

function Override({
  label,
  unit,
  placeholder,
  value,
  onChange,
  help,
}: {
  label: string;
  unit: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  help: string;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">
        {label} <span className="text-muted-foreground font-normal">({unit})</span>
      </Label>
      <Input
        type="number"
        className="h-auto w-full rounded-md bg-secondary px-[13px] py-2 font-mono text-sm"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-muted-foreground text-[11px] leading-snug">{help}</p>
    </div>
  );
}

function SocSlider({
  socPct,
  setSocPct,
  help,
}: {
  socPct: number;
  setSocPct: (v: number) => void;
  help: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label>
        Starting battery <span className="font-mono">{socPct}%</span>
      </Label>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={socPct}
        onChange={(e) => setSocPct(Number(e.target.value))}
        className="accent-primary h-2 w-full cursor-pointer"
      />
      <p className="text-muted-foreground text-xs">{help}</p>
    </div>
  );
}

export function TestView() {
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: fetchScenarios });
  const [mode, setMode] = useState<Mode>("scenario");
  const [scenario, setScenario] = useState("");
  const [at, setAt] = useState(defaultAt);
  const [recordedSoc, setRecordedSoc] = useState(true);
  const [socPct, setSocPct] = useState(70);
  const [wear, setWear] = useState("");
  const [holdScaling, setHoldScaling] = useState("");
  const [exportSpread, setExportSpread] = useState("");
  const [minExport, setMinExport] = useState("");
  const [targetSoc, setTargetSoc] = useState("");
  const [targetHold, setTargetHold] = useState("");
  const [targetPenalty, setTargetPenalty] = useState("");

  useEffect(() => {
    if (!scenario && scenarios.data?.[0]) setScenario(scenarios.data[0].id);
  }, [scenario, scenarios.data]);

  const overrides: SimOverrides = {
    wear_cost_per_kwh: numOrNull(wear),
    hold_value_scaling: numOrNull(holdScaling),
    min_battery_export_spread: numOrNull(exportSpread),
    min_battery_export_price: numOrNull(minExport),
    daily_target_soc: numOrNull(targetSoc),
    daily_target_hold_hours: numOrNull(targetHold),
    daily_target_penalty_per_kwh: numOrNull(targetPenalty),
  };

  const sim = useMutation({
    mutationFn: () =>
      mode === "scenario"
        ? runSimulation({ scenario, soc_frac: socPct / 100, overrides })
        : runHistorySimulation({
            at,
            soc_frac: recordedSoc ? null : socPct / 100,
            overrides,
          }),
  });

  const chosen = scenarios.data?.find((s) => s.id === scenario);
  const canRun = mode === "scenario" ? Boolean(scenario) : Boolean(at);
  const notes = sim.data?.meta.notes ?? [];

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Test mode</CardTitle>
          <CardDescription>
            Run the optimiser without waiting for real prices to change — against a made-up
            price scenario, or time-travelled onto data Home Assistant actually recorded.
            Nothing here touches your live plan or the inverter; it only simulates. Overrides
            let you preview a config change without saving it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Scenarios | Time travel toggle, styled like the header tab pill */}
          <nav className="flex w-fit gap-[3px] rounded-full bg-tab-bg p-[3px] max-sm:w-full">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                aria-current={mode === m.id ? "page" : undefined}
                onClick={() => setMode(m.id)}
                className={
                  "cursor-pointer rounded-full border-none px-4 py-[6px] text-[13px] font-semibold transition-all max-sm:flex-1 " +
                  (mode === m.id
                    ? "bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,.12)] dark:bg-[#2c2c2c] dark:shadow-none"
                    : "bg-transparent text-muted-foreground hover:text-foreground")
                }
              >
                {m.label}
              </button>
            ))}
          </nav>

          {mode === "scenario" ? (
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_220px]">
              <div className="space-y-1.5">
                <Label>Price scenario</Label>
                <Select value={scenario} onValueChange={setScenario}>
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Choose a scenario…" />
                  </SelectTrigger>
                  <SelectContent>
                    {(scenarios.data ?? []).map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {chosen && (
                  <p className="text-muted-foreground text-xs leading-snug">
                    {chosen.description}
                  </p>
                )}
              </div>
              <SocSlider
                socPct={socPct}
                setSocPct={setSocPct}
                help="Battery state of charge at the start."
              />
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_220px]">
              <div className="space-y-1.5">
                <Label>Go back to</Label>
                <Input
                  type="datetime-local"
                  className="h-auto w-full rounded-md bg-secondary px-[13px] py-2 font-mono text-sm"
                  value={at}
                  onChange={(e) => setAt(e.target.value)}
                />
                <p className="text-muted-foreground text-xs leading-snug">
                  Replays the prices, solar and load Home Assistant recorded from this moment
                  (hindsight, not the forecast HEM saw). The recorder keeps ~10 days by default.
                </p>
              </div>
              <div className="space-y-2.5">
                <div className="space-y-1.5">
                  <Label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      checked={recordedSoc}
                      onChange={(e) => setRecordedSoc(e.target.checked)}
                      className="accent-primary size-4 cursor-pointer"
                    />
                    Use recorded battery level
                  </Label>
                  <p className="text-muted-foreground text-xs">
                    Start from the SoC the battery actually had at that time.
                  </p>
                </div>
                {!recordedSoc && (
                  <SocSlider
                    socPct={socPct}
                    setSocPct={setSocPct}
                    help="Battery state of charge at that moment."
                  />
                )}
              </div>
            </div>
          )}

          <details className="rounded-md border border-border bg-secondary/40 px-3.5 py-2.5">
            <summary className="cursor-pointer text-[13px] font-medium text-muted-foreground select-none">
              Config overrides (optional)
            </summary>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <Override
                label="Wear cost"
                unit="$/kWh"
                placeholder="saved"
                value={wear}
                onChange={setWear}
                help="Battery throughput/degradation cost. Typical Li-ion ≈ 0.5–3c."
              />
              <Override
                label="Hold value scaling"
                unit="×"
                placeholder="saved"
                value={holdScaling}
                onChange={setHoldScaling}
                help="Multiplier on the rebuy-anchored hold value. >1 = holdier, <1 = trades more."
              />
              <Override
                label="Min battery export spread"
                unit="$/kWh"
                placeholder="saved / off"
                value={exportSpread}
                onChange={setExportSpread}
                help="Deadband: only sell battery to the grid when the feed-in beats holding by this margin."
              />
              <Override
                label="Min battery export price"
                unit="$/kWh"
                placeholder="saved / off"
                value={minExport}
                onChange={setMinExport}
                help="Hard floor: battery won't discharge to the grid below this feed-in price."
              />
              <Override
                label="Daily target SoC"
                unit="0–1"
                placeholder="saved"
                value={targetSoc}
                onChange={setTargetSoc}
                help="Soft target held from the daily target time (1 = 100%)."
              />
              <Override
                label="Daily target hold"
                unit="h"
                placeholder="saved"
                value={targetHold}
                onChange={setTargetHold}
                help="Hours to hold the target as a floor through the evening. 0 = single instant."
              />
              <Override
                label="Daily target penalty"
                unit="$/kWh·h"
                placeholder="saved"
                value={targetPenalty}
                onChange={setTargetPenalty}
                help="Willingness-to-pay per kWh-hour short of the target. Higher = fills harder."
              />
            </div>
          </details>

          <div className="flex items-center gap-3">
            <Button type="button" disabled={!canRun || sim.isPending} onClick={() => sim.mutate()}>
              {sim.isPending
                ? "Running…"
                : mode === "scenario"
                  ? "Run simulation"
                  : "Replay"}
            </Button>
            {sim.data && (
              <span className="text-muted-foreground text-xs">
                Solved in {Math.round(sim.data.solve_ms)} ms · {sim.data.solver_status}
              </span>
            )}
          </div>
          {sim.isError && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3.5 py-2.5 text-[13px] text-destructive">
              {String(sim.error)}
            </div>
          )}
        </CardContent>
      </Card>

      {notes.length > 0 && (
        <div className="rounded-lg border border-border bg-card px-3.5 py-2.5 text-[13px] text-muted-foreground">
          {notes.map((n) => (
            <div key={n} className="flex gap-1.5">
              <span aria-hidden>ℹ︎</span>
              <span>{n}</span>
            </div>
          ))}
        </div>
      )}

      {sim.data ? (
        <PlanView plan={sim.data} />
      ) : (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          {mode === "scenario" ? (
            <>
              Pick a scenario and press <span className="font-medium">Run simulation</span> to see
              the plan.
            </>
          ) : (
            <>
              Pick a moment in the past and press{" "}
              <span className="font-medium">Replay</span> to see how HEM would have planned it.
            </>
          )}
        </div>
      )}
    </>
  );
}

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { fetchScenarios, runSimulation } from "./api";
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

export function TestView() {
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: fetchScenarios });
  const [scenario, setScenario] = useState("");
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

  const sim = useMutation({
    mutationFn: () =>
      runSimulation({
        scenario,
        soc_frac: socPct / 100,
        overrides: {
          wear_cost_per_kwh: numOrNull(wear),
          hold_value_scaling: numOrNull(holdScaling),
          min_battery_export_spread: numOrNull(exportSpread),
          min_battery_export_price: numOrNull(minExport),
          daily_target_soc: numOrNull(targetSoc),
          daily_target_hold_hours: numOrNull(targetHold),
          daily_target_penalty_per_kwh: numOrNull(targetPenalty),
        },
      }),
  });

  const chosen = scenarios.data?.find((s) => s.id === scenario);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Test mode</CardTitle>
          <CardDescription>
            Run the optimiser against a made-up Amber price scenario to see how it responds —
            no waiting for real prices to change. Nothing here touches your live plan or the
            inverter; it only simulates. Overrides let you preview a config change without saving
            it.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
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
                <p className="text-muted-foreground text-xs leading-snug">{chosen.description}</p>
              )}
            </div>
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
              <p className="text-muted-foreground text-xs">Battery state of charge at the start.</p>
            </div>
          </div>

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
            <Button type="button" disabled={!scenario || sim.isPending} onClick={() => sim.mutate()}>
              {sim.isPending ? "Running…" : "Run simulation"}
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

      {sim.data ? (
        <PlanView plan={sim.data} />
      ) : (
        <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
          Pick a scenario and press <span className="font-medium">Run simulation</span> to see the
          plan.
        </div>
      )}
    </>
  );
}

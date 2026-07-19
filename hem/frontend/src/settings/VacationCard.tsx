import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type ConfigDoc, ConfigValidationError, fetchConfig, putConfig } from "@/api";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface StoredVacation {
  enabled?: boolean;
  baseline_kw?: number;
  until?: string | null;
}

function vacationOf(config: Record<string, unknown> | null): StoredVacation {
  return (config?.vacation as StoredVacation | undefined) ?? {};
}

function isActive(v: StoredVacation): boolean {
  // Naive "until" strings are local time — exactly how Date parses them.
  return v.enabled === true && (!v.until || new Date(v.until) > new Date());
}

export function fmtUntil(until: string | null | undefined): string {
  return until ? `until ${new Date(until).toLocaleString()}` : "until turned off";
}

/** Vacation mode lives outside the main settings form: enabling/disabling is
 * an immediate, targeted PUT of the last-saved config with only `vacation`
 * changed — it must not depend on (or accidentally save) unsaved form edits. */
export function VacationCard() {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig });
  const [open, setOpen] = useState(false);
  const [baseline, setBaseline] = useState<string | null>(null); // null = seed from stored
  const [until, setUntil] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const stored = vacationOf(config.data?.config ?? null);
  const active = isActive(stored);
  const expired = stored.enabled === true && !active;

  const save = useMutation({
    mutationFn: (vacation: StoredVacation) => {
      const base = config.data?.config;
      if (!base) throw new Error("configure HEM first");
      return putConfig({ ...base, vacation } as ConfigDoc);
    },
    onSuccess: () => {
      setError(null);
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void queryClient.invalidateQueries({ queryKey: ["plan"] });
    },
    onError: (e) => {
      setError(
        e instanceof ConfigValidationError
          ? e.fieldErrors.map((f) => `${f.loc}: ${f.msg}`).join("; ")
          : String(e),
      );
    },
  });

  const openDialog = () => {
    setBaseline(null);
    setUntil(null);
    setError(null);
    setOpen(true);
  };
  const baselineValue = baseline ?? String(stored.baseline_kw ?? 0.3);
  const untilValue = until ?? (stored.until ? stored.until.slice(0, 16) : "");

  return (
    <Card>
      <CardHeader>
        <CardTitle>Vacation mode</CardTitle>
        <CardDescription>
          {active ? (
            <>
              <span className="font-medium text-[#e67e22]">Active</span> — load forecast
              flattened to {stored.baseline_kw} kW, {fmtUntil(stored.until)}.
            </>
          ) : (
            <>
              Household away? Flatten the load forecast to a standby baseline so the whole
              battery is free for the market{expired && " (previous vacation has ended)"}.
            </>
          )}
        </CardDescription>
        <CardAction className="flex gap-2">
          {active ? (
            <>
              <Button type="button" variant="outline" size="sm" onClick={openDialog}>
                Edit…
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={save.isPending}
                onClick={() => save.mutate({ ...stored, enabled: false })}
              >
                Disable
              </Button>
            </>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!config.data?.configured}
              onClick={openDialog}
            >
              Enable vacation mode…
            </Button>
          )}
        </CardAction>
        {error && !open && <p className="text-destructive text-xs">{error}</p>}
      </CardHeader>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Vacation mode</DialogTitle>
            <DialogDescription>
              While active, HEM plans with a flat standby baseline instead of the learned
              load forecast — no temperature response, no load buffer — freeing the battery
              to chase spikes and cheap windows. The plan reverts to the learned forecast
              from the end time onward (or when you disable it), and{" "}
              <code>binary_sensor.hem_vacation_mode</code> reports the state to Home
              Assistant.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-1">
            <div className="grid gap-1.5">
              <Label htmlFor="vacation-baseline">Baseline load (kW)</Label>
              <Input
                id="vacation-baseline"
                type="number"
                className="w-44"
                min={0}
                step={0.05}
                value={baselineValue}
                onChange={(e) => setBaseline(e.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                Your house's standby draw — fridge, network gear, pumps. Typically 0.2–0.4
                kW; check your load sensor overnight while nothing is running.
              </p>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="vacation-until">Ends</Label>
              <Input
                id="vacation-until"
                type="datetime-local"
                className="w-56"
                value={untilValue}
                onChange={(e) => setUntil(e.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                Local time; vacation mode expires on its own — if it lands inside the
                planning horizon, the plan already covers your return evening. Leave empty
                to keep it on until you disable it.
              </p>
            </div>
            {error && <p className="text-destructive text-xs">{error}</p>}
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="ghost">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              disabled={save.isPending}
              onClick={() =>
                save.mutate({
                  enabled: true,
                  baseline_kw: Number(baselineValue),
                  until: untilValue || null,
                })
              }
            >
              {save.isPending ? "Saving…" : active ? "Update" : "Enable"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

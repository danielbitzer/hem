// Test-mode sandbox settings: the solver-relevant config sections (battery,
// grid, optimizer, spike) as an editable copy of the live config. Edits apply
// to simulations only — every test run sends the sandbox sections with the
// request — until the user explicitly promotes them with "Apply to live".
// State lives in App (not here) so it survives panel toggles and mode switches.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type ConfigResponse, putConfig } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { refetchPlanUntilFresh } from "@/planRefresh";
import {
  buildDefaults,
  FieldRow,
  type FormValues,
  SANDBOX_SECTION_IDS,
  sandboxDoc,
} from "./form";
import { getPath, SECTIONS, setPath } from "./spec";

const SANDBOX_SECTIONS = SECTIONS.filter((s) =>
  (SANDBOX_SECTION_IDS as readonly string[]).includes(s.id),
);

export function SandboxPanel({
  values,
  onChange,
  errors,
  onErrors,
  liveConfig,
  dirty,
}: {
  values: FormValues;
  onChange: (v: FormValues) => void;
  /** Per-field validation errors from the last simulate call (422). */
  errors: Record<string, string>;
  onErrors: (e: Record<string, string>) => void;
  liveConfig: Record<string, unknown> | null;
  /** Sandbox differs from a fresh copy of the live config (computed in App —
   * it disables Reset/Apply when there is nothing to reset or apply). */
  dirty: boolean;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [applied, setApplied] = useState(false);
  const [applyError, setApplyError] = useState("");

  const apply = useMutation({
    mutationFn: async () => {
      const live = queryClient.getQueryData<ConfigResponse>(["config"])?.config;
      if (!live) throw new Error("live config unavailable");
      // The full live document with only the sandbox sections replaced —
      // entities, vacation, enabled etc. stay exactly as saved.
      await putConfig({ ...live, ...sandboxDoc(values) });
    },
    onSuccess: () => {
      setApplied(true);
      setApplyError("");
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void refetchPlanUntilFresh(queryClient);
    },
    onError: (e) => setApplyError(String(e)),
  });

  const edit = (path: string, v: string | boolean) => {
    const next = structuredClone(values);
    setPath(next, path, v);
    onChange(next);
    setApplied(false);
    if (path in errors) {
      const { [path]: _dropped, ...rest } = errors;
      onErrors(rest);
    }
  };

  return (
    <div className="space-y-4">
      {SANDBOX_SECTIONS.map((section) => (
        <Card key={section.id}>
          <CardHeader>
            <CardTitle>{section.title}</CardTitle>
            <CardDescription>{section.description}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="divide-y">
              {section.fields.map((spec) => (
                <FieldRow
                  key={spec.path}
                  spec={spec}
                  value={getPath(values, spec.path) as string | boolean}
                  onChange={(v) => edit(spec.path, v)}
                  error={errors[spec.path]}
                  entities={[]}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      ))}

      {applyError && (
        <div className="border-destructive text-destructive rounded-xl border px-4 py-3 text-sm">
          {applyError}
        </div>
      )}

      <div className="bg-background/95 sticky bottom-0 -mx-1 border-t px-1 py-3 backdrop-blur">
        {confirming ? (
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm">Overwrite the live settings with these sections?</span>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={apply.isPending}
                onClick={() => {
                  apply.mutate();
                  setConfirming(false);
                }}
              >
                Apply
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={!dirty}
              onClick={() => {
                if (liveConfig) onChange(buildDefaults(liveConfig));
                onErrors({});
                setApplied(false);
                setApplyError("");
              }}
            >
              Reset to live
            </Button>
            <Button type="button" disabled={!dirty || apply.isPending} onClick={() => setConfirming(true)}>
              {apply.isPending ? "Applying…" : "Apply to live…"}
            </Button>
            {applied && <span className="text-muted-foreground text-sm">Applied to live settings.</span>}
            {!applied && dirty && (
              <span className="text-muted-foreground text-xs">Differs from live</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

import { useForm } from "@tanstack/react-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type ConfigDoc,
  type ConfigResponse,
  ConfigValidationError,
  type Entity,
  type FieldError,
  fetchConfig,
  fetchEntities,
  putConfig,
} from "@/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { refetchPlanUntilFresh } from "@/planRefresh";
import { setThemePref, type ThemePref, useThemePref } from "@/theme";
import { EntityPicker } from "./EntityPicker";
import { ALL_FIELDS, type FieldSpec, getPath, SECTIONS, setPath } from "./spec";
import { VacationCard } from "./VacationCard";

/** Form state mirrors the config document's shape; numbers are input strings
 * until submit so partial typing ("0.", "-") never fights the user. */
type FormValues = Record<string, unknown>;

function buildDefaults(config: Record<string, unknown> | null): FormValues {
  const values: FormValues = { enabled: config?.enabled === true };
  for (const f of ALL_FIELDS) {
    const raw = config ? getPath(config, f.path) : undefined;
    let v: string | boolean;
    if (f.kind === "boolean") {
      v = raw === undefined ? f.default === true : raw === true;
    } else if (f.kind === "select") {
      v = raw === undefined ? String(f.default ?? "") : String(raw);
    } else {
      // Defaults are shown as grey placeholders, not pre-filled values: an
      // empty input means "use the default" (a stored value EQUAL to the
      // default also renders as the placeholder — same semantics, and it
      // keeps default-vs-customized visually distinct after reloads).
      v = raw === undefined ? "" : String(raw);
      if (f.kind === "time") v = v.slice(0, 5); // pydantic dumps "15:00:00"
      // Percent fields store fractions but display ×100 (spec defaults are
      // already in display units). Round away float artifacts (0.07*100).
      if (f.percent && typeof raw === "number") {
        v = String(Math.round(raw * 100 * 1e6) / 1e6);
      }
      if (f.default !== undefined && v === String(f.default)) v = "";
    }
    setPath(values, f.path, v);
  }
  return values;
}

function toDoc(values: FormValues): ConfigDoc {
  const doc: ConfigDoc = { enabled: values.enabled === true };
  for (const f of ALL_FIELDS) {
    const v = getPath(values, f.path);
    if (f.kind === "boolean") {
      setPath(doc, f.path, v === true);
      continue;
    }
    const s = String(v ?? "").trim();
    if (f.kind === "number") {
      // empty optional -> omit, the server default applies; non-numeric text
      // goes through as-is so the server's per-field error lands on the input
      if (s !== "") {
        const n = Number(s);
        // percent fields display ×100; store the fraction the server expects
        setPath(doc, f.path, Number.isNaN(n) ? s : f.percent ? n / 100 : n);
      }
    } else if (f.kind === "text") {
      // terminal_soc_value: "auto" | number
      if (s !== "") setPath(doc, f.path, s !== "auto" && !Number.isNaN(Number(s)) ? Number(s) : s);
    } else if (f.kind === "time") {
      if (s !== "") setPath(doc, f.path, s); // "16:30"; empty -> server default
    } else {
      setPath(doc, f.path, s); // entity ids ("" = not used) and selects
    }
  }
  return doc;
}

/** Attach each server error to the field whose path prefixes its loc (union
 * validators append discriminators, e.g. `...terminal_soc_value.literal['auto']`);
 * the rest render above the save bar. */
function mapServerErrors(errors: FieldError[]): {
  byField: Record<string, string>;
  general: string[];
} {
  const byField: Record<string, string> = {};
  const general: string[] = [];
  for (const err of errors) {
    const field = ALL_FIELDS.find((f) => err.loc === f.path || err.loc.startsWith(`${f.path}.`));
    if (field && !byField[field.path]) byField[field.path] = err.msg;
    else if (!field) general.push(`${err.loc}: ${err.msg}`);
  }
  return { byField, general };
}

export function SettingsView() {
  const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig });
  if (config.isPending) return <div className="p-6 text-center">loading…</div>;
  if (config.isError) {
    return <div className="p-6 text-center text-destructive">{String(config.error)}</div>;
  }
  return <SettingsForm initialConfig={config.data.config} />;
}

function SettingsForm({ initialConfig }: { initialConfig: Record<string, unknown> | null }) {
  const queryClient = useQueryClient();
  const entities = useQuery({ queryKey: ["entities"], queryFn: fetchEntities, retry: 1 });
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [generalErrors, setGeneralErrors] = useState<string[]>([]);
  const [saved, setSaved] = useState(false);

  const save = useMutation({
    mutationFn: putConfig,
    onSuccess: () => {
      setServerErrors({});
      setGeneralErrors([]);
      setSaved(true);
      void queryClient.invalidateQueries({ queryKey: ["config"] });
      void refetchPlanUntilFresh(queryClient);
    },
    onError: (e) => {
      setSaved(false);
      if (e instanceof ConfigValidationError) {
        const { byField, general } = mapServerErrors(e.fieldErrors);
        setServerErrors(byField);
        setGeneralErrors(general);
      } else {
        setGeneralErrors([String(e)]);
      }
    },
  });

  const form = useForm({
    defaultValues: buildDefaults(initialConfig),
    onSubmit: async ({ value }) => {
      setSaved(false);
      const doc = toDoc(value);
      // The vacation section is managed by VacationCard, not this form —
      // carry the last-saved value through so a form save can't reset it.
      const latest = queryClient.getQueryData<ConfigResponse>(["config"]);
      if (latest?.config?.vacation !== undefined) doc.vacation = latest.config.vacation;
      await save.mutateAsync(doc).catch(() => undefined); // surfaced via mutation state
    },
  });

  return (
    <form
      // w-full matters: with mx-auto the flex parent can't stretch this
      // (auto cross-axis margins disable it), and shrink-to-fit sizing is
      // floored by min-content — one wide entity label would widen the form
      // past the viewport and every card with it.
      className="mx-auto w-full max-w-3xl space-y-4"
      // The server (pydantic) is the validation authority — native browser
      // validation would reject values it accepts (e.g. step mismatches like
      // a 0.12 daily target against step=0.05) with a bubble instead of our
      // error UI. min/max/step stay on the inputs as spinner hints only.
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void form.handleSubmit();
      }}
    >
      <Card>
        <CardHeader>
          <CardTitle>HEM enabled</CardTitle>
          <CardDescription>
            The master switch. While off (or before first configuration), HEM runs no planning
            cycles and publishes <code>sensor.hem_status</code> as something other than{" "}
            <code>ok</code> — your actuator automation's failsafe then keeps the inverter in
            plain self-consumption.
          </CardDescription>
          <CardAction>
            <form.Field name="enabled">
              {(field) => (
                <Switch
                  checked={field.state.value === true}
                  onCheckedChange={(checked) => field.handleChange(checked)}
                />
              )}
            </form.Field>
          </CardAction>
        </CardHeader>
      </Card>

      <VacationCard />

      {SECTIONS.map((section) => (
        <Card key={section.id}>
          <CardHeader>
            <CardTitle>{section.title}</CardTitle>
            <CardDescription>{section.description}</CardDescription>
          </CardHeader>
          <CardContent>
            {section.id === "entities" && entities.isError && (
              <p className="text-destructive mb-2 text-xs">
                Entity list unavailable ({String(entities.error)}) — type entity IDs manually.
              </p>
            )}
            <div className="divide-y">
              {section.fields.map((spec) => (
                <form.Field
                  key={spec.path}
                  name={spec.path}
                  validators={{
                    onSubmit: ({ value }) =>
                      spec.required && String(value ?? "").trim() === ""
                        ? "Required"
                        : undefined,
                  }}
                >
                  {(field) => (
                    <FieldRow
                      spec={spec}
                      value={field.state.value as string | boolean}
                      onChange={(v) => {
                        field.handleChange(v);
                        setServerErrors((prev) => {
                          if (!(spec.path in prev)) return prev;
                          const { [spec.path]: _dropped, ...rest } = prev;
                          return rest;
                        });
                      }}
                      error={field.state.meta.errors[0]?.toString() ?? serverErrors[spec.path]}
                      entities={entities.data ?? []}
                    />
                  )}
                </form.Field>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}

      <AppearanceCard />

      {generalErrors.length > 0 && (
        <div className="border-destructive text-destructive rounded-xl border px-4 py-3 text-sm">
          {generalErrors.map((msg) => (
            <div key={msg}>{msg}</div>
          ))}
        </div>
      )}

      <div className="bg-background/95 sticky bottom-0 -mx-1 flex items-center gap-3 border-t px-1 py-3 backdrop-blur">
        <form.Subscribe selector={(s) => s.isSubmitting}>
          {(isSubmitting) => (
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Saving…" : "Save & apply"}
            </Button>
          )}
        </form.Subscribe>
        {saved && (
          <span className="text-muted-foreground text-sm">
            Saved — applied before the next cycle.
          </span>
        )}
        {!saved && (Object.keys(serverErrors).length > 0 || generalErrors.length > 0) && (
          <span className="text-destructive text-sm">Not saved — fix the errors above.</span>
        )}
      </div>
    </form>
  );
}

/** Client-side only — applies instantly and is stored in this browser
 * (localStorage), not in the HEM config, so it sits outside the form/save. */
function AppearanceCard() {
  const pref = useThemePref();
  return (
    <Card>
      <CardHeader className="max-sm:flex max-sm:flex-col max-sm:gap-2">
        <CardTitle>Theme</CardTitle>
        <CardDescription>
          Light, dark, or follow this device's preference. Applies immediately and is
          remembered in this browser only.
        </CardDescription>
        <CardAction className="max-sm:w-full">
          <Select value={pref} onValueChange={(v) => setThemePref(v as ThemePref)}>
            <SelectTrigger className="w-32 max-sm:w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="system">System</SelectItem>
              <SelectItem value="light">Light</SelectItem>
              <SelectItem value="dark">Dark</SelectItem>
            </SelectContent>
          </Select>
        </CardAction>
      </CardHeader>
    </Card>
  );
}

function FieldRow({
  spec,
  value,
  onChange,
  error,
  entities,
}: {
  spec: FieldSpec;
  value: string | boolean;
  onChange: (v: string | boolean) => void;
  error: string | undefined;
  entities: Entity[];
}) {
  return (
    <div className="grid gap-1.5 py-3 sm:grid-cols-[210px_minmax(0,1fr)] sm:gap-x-6">
      <Label className="pt-1.5 leading-snug">
        {spec.label}
        {spec.required && <span className="text-destructive"> *</span>}
        {spec.unit && <span className="text-muted-foreground font-normal"> ({spec.unit})</span>}
      </Label>
      {/* min-w-0: let the cell shrink below its content so the entity
          picker's long selected label truncates instead of setting the
          grid track (and the whole page) wider than the screen */}
      <div className="min-w-0 space-y-1">
        {spec.kind === "entity" && (
          <EntityPicker
            value={String(value)}
            onChange={onChange}
            entities={entities}
            domains={spec.domains ?? []}
            optional={spec.optional}
            invalid={!!error}
          />
        )}
        {(spec.kind === "number" || spec.kind === "text" || spec.kind === "time") && (
          <Input
            type={spec.kind === "text" ? "text" : spec.kind}
            className="h-auto w-40 rounded-md bg-secondary px-[13px] py-2.5 font-mono text-sm"
            value={String(value)}
            placeholder={typeof spec.default === "string" ? spec.default : undefined}
            min={spec.min}
            max={spec.max}
            step={spec.step}
            aria-invalid={!!error}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {spec.kind === "boolean" && (
          <Switch checked={value === true} onCheckedChange={onChange} />
        )}
        {spec.kind === "select" && (
          <Select value={String(value)} onValueChange={onChange}>
            <SelectTrigger className="w-full max-w-md">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {spec.options?.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {error && <p className="text-destructive text-xs">{error}</p>}
        <p className="text-muted-foreground text-xs">{spec.help}</p>
      </div>
    </div>
  );
}

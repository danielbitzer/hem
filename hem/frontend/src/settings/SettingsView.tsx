import { useForm } from "@tanstack/react-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  type ConfigDoc,
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
import { EntityPicker } from "./EntityPicker";
import { ALL_FIELDS, type FieldSpec, getPath, SECTIONS, setPath } from "./spec";

/** Form state mirrors the config document's shape; numbers are input strings
 * until submit so partial typing ("0.", "-") never fights the user. */
type FormValues = Record<string, unknown>;

function buildDefaults(config: Record<string, unknown> | null): FormValues {
  const values: FormValues = { enabled: config?.enabled === true };
  for (const f of ALL_FIELDS) {
    const raw = config ? getPath(config, f.path) : undefined;
    const v: string | boolean =
      f.kind === "boolean"
        ? raw === undefined
          ? f.default === true
          : raw === true
        : raw === undefined
          ? String(f.default ?? "")
          : String(raw);
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
      if (s !== "") setPath(doc, f.path, Number.isNaN(Number(s)) ? s : Number(s));
    } else if (f.kind === "text") {
      // terminal_soc_value: "auto" | number
      if (s !== "") setPath(doc, f.path, s !== "auto" && !Number.isNaN(Number(s)) ? Number(s) : s);
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
      void queryClient.invalidateQueries({ queryKey: ["plan"] });
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
      await save.mutateAsync(toDoc(value)).catch(() => undefined); // surfaced via mutation state
    },
  });

  return (
    <form
      className="mx-auto max-w-3xl space-y-4"
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
    <div className="grid gap-1.5 py-3 sm:grid-cols-[230px_minmax(0,1fr)] sm:gap-x-6">
      <Label className="pt-1.5 leading-snug">
        {spec.label}
        {spec.unit && <span className="text-muted-foreground font-normal"> ({spec.unit})</span>}
      </Label>
      <div className="space-y-1">
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
        {(spec.kind === "number" || spec.kind === "text") && (
          <Input
            type={spec.kind === "number" ? "number" : "text"}
            className="w-44"
            value={String(value)}
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

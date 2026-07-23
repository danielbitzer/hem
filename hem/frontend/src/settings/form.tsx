// Shared machinery between the live Settings form and the test-mode sandbox
// panel: config-doc <-> form-values conversion, server-error mapping, and the
// per-field row renderer. The two forms differ in chrome and lifecycle (save
// vs simulate) but must agree exactly on field semantics.

import type { Entity, FieldError, SandboxConfig } from "@/api";
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
import { ALL_FIELDS, type FieldSpec, getPath, setPath } from "./spec";

/** Form state mirrors the config document's shape; numbers are input strings
 * until submit so partial typing ("0.", "-") never fights the user. */
export type FormValues = Record<string, unknown>;

/** The config sections the test-mode sandbox edits and sends with each
 * simulation. Everything else (entities, load learning, vacation, enabled)
 * always comes from the live config. Mirrors SANDBOX_SECTIONS in web/app.py. */
export const SANDBOX_SECTION_IDS = ["battery", "grid", "optimizer", "spike"] as const;

export function buildDefaults(config: Record<string, unknown> | null): FormValues {
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

export function toDoc(values: FormValues): Record<string, unknown> {
  const doc: Record<string, unknown> = { enabled: values.enabled === true };
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

/** The sandbox sections of a form-values object, in config-doc shape — what
 * test mode sends as the simulation's "config". */
export function sandboxDoc(values: FormValues): SandboxConfig {
  const doc = toDoc(values);
  const out: SandboxConfig = {};
  for (const id of SANDBOX_SECTION_IDS) {
    if (doc[id] !== undefined) out[id] = doc[id];
  }
  return out;
}

/** Attach each server error to the field whose path prefixes its loc (union
 * validators append discriminators, e.g. `...terminal_soc_value.literal['auto']`);
 * the rest render above the action bar. */
export function mapServerErrors(errors: FieldError[]): {
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

export function FieldRow({
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

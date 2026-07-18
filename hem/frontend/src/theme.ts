import { useSyncExternalStore } from "react";
import type { Action } from "./api";

export const ACTION_COLORS: Record<Action, string> = {
  charge: "#2e86de",
  discharge: "#10ac84",
  idle: "var(--muted)", // follows the theme, like the old .action-idle class
  no_charge: "#8e44ad",
  curtail: "#e67e22",
};

export const SERIES = {
  buy: "#e74c3c",
  sell: "#10ac84",
  pv: "#f39c12",
  load: "#8e8e93",
  battery: "#2e86de",
  gridImport: "#e74c3c",
  gridExport: "#10ac84",
} as const;

// Shared fixed y-axis gutter + right margin so every chart's plot area (and
// the CSS-positioned mode strip) spans the identical x range. The strip only
// knows GUTTER and RIGHT_MARGIN, so CHART_MARGIN.left MUST stay 0 and the
// YAxis width MUST stay GUTTER — a Recharts plot's left edge is
// margin.left + axis width, and the strip would silently drift otherwise.
export const GUTTER = 36;
export const RIGHT_MARGIN = 16;
export const CHART_MARGIN = { top: 8, right: RIGHT_MARGIN, bottom: 0, left: 0 };

const query = window.matchMedia("(prefers-color-scheme: dark)");

export function useDark(): boolean {
  return useSyncExternalStore(
    (notify) => {
      query.addEventListener("change", notify);
      return () => query.removeEventListener("change", notify);
    },
    () => query.matches,
  );
}

// SVG attributes can't resolve CSS variables, so chart strokes restate the
// index.css palette (--border / --muted values) per theme — keep in sync.
export function gridStroke(dark: boolean): string {
  return dark ? "#2c2c30" : "#e3e3e8";
}

export function cursorStroke(dark: boolean): string {
  return dark ? "#6e6e78" : "#90909a";
}

export function idleSegmentColor(dark: boolean): string {
  return dark ? "#5c5c66" : "#c8c8cd";
}

export function fmtDayTime(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

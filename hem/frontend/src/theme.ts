import { useSyncExternalStore } from "react";
import type { Action } from "./api";

export const ACTION_COLORS: Record<Action, string> = {
  charge: "#2e86de",
  discharge: "#10ac84",
  idle: "#9a9aa2",
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
// the CSS-positioned mode strip) spans the identical x range.
export const GUTTER = 56;
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

export function gridStroke(dark: boolean): string {
  return dark ? "#2c2c30" : "#e3e3e8";
}

export function idleSegmentColor(dark: boolean): string {
  return dark ? "#5c5c66" : "#c8c8cd";
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function fmtDayTime(ms: number): string {
  const d = new Date(ms);
  const hm = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  return `${WEEKDAYS[d.getDay()]} ${hm}`;
}

export function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

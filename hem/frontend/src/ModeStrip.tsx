import { useState } from "react";
import type { PlanInterval } from "./api";
import { Card } from "./charts";
import { useHoverT } from "./hover";
import {
  ACTION_COLORS,
  fmtDayTime,
  GUTTER,
  idleSegmentColor,
  RIGHT_MARGIN,
  useDark,
} from "./theme";

interface Segment {
  action: string;
  startMs: number;
  endMs: number;
}

function mergeSegments(intervals: PlanInterval[]): Segment[] {
  const out: Segment[] = [];
  for (const iv of intervals) {
    const startMs = Date.parse(iv.start);
    const endMs = Date.parse(iv.end);
    const last = out[out.length - 1];
    if (last && last.action === iv.action && last.endMs === startMs) {
      last.endMs = endMs;
    } else {
      out.push({ action: iv.action, startMs, endMs });
    }
  }
  return out;
}

/**
 * Custom timeline strip (Recharts has no rangeBar): colored segments per
 * contiguous action run, positioned by % of the shared time domain so it
 * aligns with the charts' plot areas (same gutter + right margin). Shows its
 * own hover tooltip and the crosshair synced from the charts.
 */
export function ModeStrip({
  intervals,
  domain,
}: {
  intervals: PlanInterval[];
  domain: [number, number];
}) {
  const dark = useDark();
  const hoverT = useHoverT();
  const [local, setLocal] = useState<{ x: number; seg: Segment } | null>(null);
  const [t0, tEnd] = domain;
  const span = tEnd - t0;
  const segments = mergeSegments(intervals);
  const pct = (ms: number) => (100 * (ms - t0)) / span;

  const cursorPct = hoverT !== null && hoverT >= t0 && hoverT <= tEnd ? pct(hoverT) : null;

  return (
    <Card title="Planned mode">
      <div style={{ paddingLeft: GUTTER, paddingRight: RIGHT_MARGIN }}>
        <div
          className="relative h-7"
          onMouseMove={(e) => {
            const box = e.currentTarget.getBoundingClientRect();
            const frac = (e.clientX - box.left) / box.width;
            const ms = t0 + frac * span;
            const seg = segments.find((s) => ms >= s.startMs && ms < s.endMs);
            setLocal(seg ? { x: e.clientX - box.left, seg } : null);
          }}
          onMouseLeave={() => setLocal(null)}
        >
          {segments.map((seg) => (
            <div
              key={seg.startMs}
              className="absolute top-1 bottom-1 rounded-[3px]"
              style={{
                left: `${pct(Math.max(seg.startMs, t0))}%`,
                width: `${pct(Math.min(seg.endMs, tEnd)) - pct(Math.max(seg.startMs, t0))}%`,
                background:
                  seg.action === "idle"
                    ? idleSegmentColor(dark)
                    : (ACTION_COLORS[seg.action as keyof typeof ACTION_COLORS] ?? "#8e8e93"),
              }}
            />
          ))}
          {cursorPct !== null && (
            <div
              className="pointer-events-none absolute top-0 bottom-0 border-l border-dashed"
              style={{ left: `${cursorPct}%`, borderColor: dark ? "#6e6e78" : "#90909a" }}
            />
          )}
          {local && (
            <div
              className="pointer-events-none absolute -top-1 z-10 -translate-y-full rounded-lg border border-edge bg-card px-3 py-1.5 text-xs shadow-sm"
              style={{ left: Math.min(Math.max(local.x, 40), 9999), transform: "translate(-50%, -100%)" }}
            >
              <span className="font-semibold">{local.seg.action.replace("_", " ")}</span>
              <span className="text-muted">
                {" "}
                {fmtDayTime(local.seg.startMs)} → {fmtDayTime(local.seg.endMs)}
              </span>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

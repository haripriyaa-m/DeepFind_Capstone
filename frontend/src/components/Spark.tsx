import { useState } from "react";
import { Sample } from "../lib/engine";

/**
 * Single-series sparkline (0–100%) with a dashed limit line and a hover readout.
 * Colour is one series tone; the numbers themselves stay in text ink.
 */
export function Spark({ data, field, limit, label }: {
  data: Sample[]; field: "cpu" | "ram"; limit?: number; label: string;
}) {
  const W = 220, H = 44, pad = 3;
  const [hover, setHover] = useState<number | null>(null);
  const n = data.length;
  // newest reading sits at the right edge; the window is 60 readings wide
  const x = (i: number) => pad + ((60 - n + i) / 59) * (W - pad * 2);
  const y = (v: number) => H - pad - (Math.max(0, Math.min(100, v)) / 100) * (H - pad * 2);
  const pts = data.map((d, i) => `${x(i).toFixed(1)},${y(d[field]).toFixed(1)}`).join(" ");
  const area = n > 1 ? `M${x(0)},${H - pad} L${pts.replace(/ /g, " L")} L${x(n - 1)},${H - pad} Z` : "";
  const h = hover != null ? data[hover] : null;

  return (
    <div className="spark" aria-label={`${label} over the last ${n} readings`}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
        onMouseMove={(e) => {
          if (n < 2) return;
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - r.left) / r.width) * W;
          let best = 0, bd = Infinity;
          for (let i = 0; i < n; i++) { const d = Math.abs(x(i) - px); if (d < bd) { bd = d; best = i; } }
          setHover(best);
        }}
        onMouseLeave={() => setHover(null)}>
        {limit != null && (
          <line x1={pad} x2={W - pad} y1={y(limit)} y2={y(limit)} className="spark-limit" />
        )}
        {area && <path d={area} className="spark-area" />}
        {n > 1 && <polyline points={pts} className="spark-line" />}
        {h && hover != null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={pad} y2={H - pad} className="spark-cross" />
            <circle cx={x(hover)} cy={y(h[field])} r="3.2" className="spark-dot" />
          </>
        )}
      </svg>
      {h && (
        <div className="spark-tip">
          <b>{Math.round(h[field])}%</b> {label} · {Math.max(0, Math.round((Date.now() - h.t) / 1000))}s ago
        </div>
      )}
    </div>
  );
}

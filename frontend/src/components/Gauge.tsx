import type { CSSProperties } from "react";

type GaugeProps = {
  value: number | null;
  label?: string;
  size?: number;
  min?: number;
  max?: number;
  recommendation?: string;
};

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function scoreColor(value: number | null, rec?: string) {
  if (rec === "BUY" || (value != null && value > 0.05)) return "#4caf82";
  if (rec === "SELL" || (value != null && value < -0.05)) return "#c45c5c";
  return "#c5d0da";
}

/** Semi-circular dashboard gauge for trade_score ∈ [min, max]. */
export default function Gauge({
  value,
  label,
  size = 160,
  min = -1,
  max = 1,
  recommendation,
}: GaugeProps) {
  const v = value == null ? 0 : clamp(value, min, max);
  const t = (v - min) / (max - min);
  // Needle defaults pointing up; -90° → SELL(left), 0° → HOLD, +90° → BUY(right)
  const needleRotate = -90 + t * 180;

  const color = scoreColor(value, recommendation);
  const display = value == null ? "—" : value.toFixed(2);
  const ring = Math.max(14, Math.round(size * 0.12));
  const needleH = size / 2 - ring * 0.85;

  return (
    <div className="gauge" style={{ width: size + 24 }}>
      <div
        className="gauge-dial"
        style={
          {
            width: size,
            height: size / 2 + 14,
            margin: "12px auto 0",
            "--gauge-ring": `${ring}px`,
          } as CSSProperties
        }
      >
        <div className="gauge-arc-clip" style={{ height: size / 2 + 2 }}>
          <div
            className="gauge-arc"
            style={{
              width: size,
              height: size,
              // from 270deg = 9 o'clock; clockwise through 12 to 3 = upper semicircle
              background: `conic-gradient(
                from 270deg,
                #c45c5c 0deg 60deg,
                #7a8b9c 60deg 120deg,
                #4caf82 120deg 180deg,
                transparent 180deg 360deg
              )`,
            }}
          />
        </div>

        <div
          className="gauge-needle"
          style={{
            height: needleH,
            background: color,
            transform: `translateX(-50%) rotate(${needleRotate}deg)`,
          }}
        />
        <div className="gauge-pivot" style={{ background: color }}>
          <span />
        </div>

        <span className="gauge-tick-label sell">SELL</span>
        <span className="gauge-tick-label hold">HOLD</span>
        <span className="gauge-tick-label buy">BUY</span>
      </div>

      <div className="gauge-value" style={{ color }}>
        {recommendation || display}
      </div>
      {label && <div className="gauge-label">{label}</div>}
      {value != null && recommendation && (
        <div className="gauge-score muted">score {display}</div>
      )}
    </div>
  );
}

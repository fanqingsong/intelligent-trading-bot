import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

export type SignalRow = Record<string, unknown>;

type Props = {
  rows: SignalRow[];
  height?: number;
};

function toDayTime(raw: unknown): Time | null {
  if (raw == null) return null;
  const s = String(raw);
  // Prefer calendar date from the string to avoid UTC/local day shift (A-share daily bars)
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  if (m) return m[1] as Time;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${mo}-${day}` as Time;
}

function num(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function isTrue(v: unknown): boolean {
  return v === true || v === "true" || v === 1 || v === "1";
}

function buildCandles(rows: SignalRow[]) {
  const candles: { time: Time; open: number; high: number; low: number; close: number }[] = [];
  const markers: SeriesMarker<Time>[] = [];
  const seen = new Set<string>();

  for (const row of rows) {
    const time = toDayTime(row.timestamp ?? row.close_time);
    const close = num(row.close);
    if (!time || close == null) continue;
    // Fall back to close-only bars when OHLC incomplete
    const open = num(row.open) ?? close;
    const high = num(row.high) ?? Math.max(open, close);
    const low = num(row.low) ?? Math.min(open, close);

    const key = String(time);
    if (seen.has(key)) {
      const idx = candles.findIndex((c) => c.time === time);
      if (idx >= 0) candles[idx] = { time, open, high, low, close };
    } else {
      seen.add(key);
      candles.push({ time, open, high, low, close });
    }
  }

  candles.sort((a, b) => String(a.time).localeCompare(String(b.time)));

  // Markers from vote columns (preferred) or per-row vote_label
  for (const row of rows) {
    const time = toDayTime(row.timestamp ?? row.close_time);
    if (!time) continue;

    const vote = String(row.vote_label ?? "").toUpperCase();
    const buy = isTrue(row.buy_signal_vote) || vote === "BUY";
    const sell = isTrue(row.sell_signal_vote) || vote === "SELL";

    if (buy && !sell) {
      markers.push({
        time,
        position: "belowBar",
        shape: "arrowUp",
        color: "#4caf82",
        text: "BUY",
      });
    } else if (sell && !buy) {
      markers.push({
        time,
        position: "aboveBar",
        shape: "arrowDown",
        color: "#c45c5c",
        text: "SELL",
      });
    }
  }

  // Deduplicate markers by time+text, keep last
  const markerMap = new Map<string, SeriesMarker<Time>>();
  for (const m of markers) {
    markerMap.set(`${m.time}:${m.text}`, m);
  }
  const uniqueMarkers = [...markerMap.values()].sort((a, b) =>
    String(a.time).localeCompare(String(b.time)),
  );

  return { candles, markers: uniqueMarkers };
}

export default function SignalChart({ rows, height = 380 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // Always mount the canvas so chart init is not skipped when rows load later
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#121820" },
        textColor: "#8b9aab",
      },
      grid: {
        vertLines: { color: "rgba(42,53,68,0.6)" },
        horzLines: { color: "rgba(42,53,68,0.6)" },
      },
      rightPriceScale: { borderColor: "#2a3544" },
      timeScale: { borderColor: "#2a3544", timeVisible: false },
      crosshair: { mode: 0 },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#4caf82",
      downColor: "#c45c5c",
      borderUpColor: "#4caf82",
      borderDownColor: "#c45c5c",
      wickUpColor: "#4caf82",
      wickDownColor: "#c45c5c",
    });

    const markerApi = createSeriesMarkers(series, []);

    chartRef.current = chart;
    seriesRef.current = series;
    markersRef.current = markerApi;

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(el);
    chart.applyOptions({ width: el.clientWidth });

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current || !markersRef.current || !chartRef.current) return;
    const { candles, markers } = buildCandles(rows);
    seriesRef.current.setData(candles);
    markersRef.current.setMarkers(markers);
    chartRef.current.timeScale().fitContent();
  }, [rows, height]);

  return (
    <div className="signal-chart">
      <div className="signal-chart-legend">
        <span className="rec buy">▲ BUY</span>
        <span className="rec sell">▼ SELL</span>
        <span className="muted">多数投票买卖点</span>
      </div>
      {!rows.length && (
        <p className="muted">暂无可用的 OHLC / 信号数据绘制 K 线</p>
      )}
      <div ref={containerRef} className="signal-chart-canvas" style={{ height }} />
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import type { KlineData, RealtimeData } from "../api/client";
import { api } from "../api/client";
import { useChatStore } from "../stores/chatStore";
import { CheckIcon, PlusIcon, ArrowUpIcon, ArrowDownIcon } from "./Icon";

function fmtVol(v: number) { return v >= 1e8 ? (v / 1e8).toFixed(2) + "亿手" : v >= 1e4 ? (v / 1e4).toFixed(0) + "万手" : v + "手"; }

const MA_COLORS: Record<string, string> = { ma5: "#fbbf24", ma10: "#60a5fa", ma20: "#a78bfa", ma60: "#22d3ee" };
const EX_META: Record<string, { label: string; cls: string }> = {
  sh: { label: "沪", cls: "text-red-400 bg-red-400/10 border-red-400/20" },
  sz: { label: "深", cls: "text-green-400 bg-green-400/10 border-green-400/20" },
  bj: { label: "京", cls: "text-amber-400 bg-amber-400/10 border-amber-400/20" },
};
const signalMeta = (s?: string): { label: string; dot: string } => {
  const v = (s || "").toLowerCase();
  if (/(bear|空|跌|down)/.test(v)) return { label: "偏空", dot: "#34d399" };
  if (/(bull|多|涨|up|牛)/.test(v)) return { label: "偏多", dot: "#f87171" };
  return { label: s || "中性", dot: "#64748b" };
};
const dedupe = (arr?: number[]) => {
  if (!arr?.length) return undefined;
  return [...new Set(arr.map((n) => +n.toFixed(2)))].join(" / ");
};

const Cell = ({ l, v, c }: { l: string; v?: string; c?: string }) => (
  <div className="flex items-baseline justify-between gap-1 min-w-0">
    <span className="text-text-muted flex-shrink-0">{l}</span>
    <span className={`font-data truncate ${c || "text-text-secondary"}`} title={v}>{v ?? "-"}</span>
  </div>
);
const Ind = ({ l, v }: { l: string; v?: string | number }) => (
  <div className="flex justify-between gap-2 bg-input-bg border border-border rounded-lg px-2 py-1.5 min-w-0">
    <span className="text-text-muted flex-shrink-0">{l}</span>
    <span className="font-data text-text-secondary truncate" title={String(v ?? "")}>{v ?? "-"}</span>
  </div>
);

export default function StockDetail({ symbol, name }: { symbol: string; name: string; onClose: () => void }) {
  const [kline, setKline] = useState<KlineData | null>(null);
  const [rt, setRt] = useState<RealtimeData | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const priceRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState(365);
  const [orders, setOrders] = useState<any[]>([]);
  const watchlist = useChatStore((s) => s.watchlist);
  const addToWatchlist = useChatStore((s) => s.addToWatchlist);
  const removeFromWatchlist = useChatStore((s) => s.removeFromWatchlist);
  const watched = watchlist.some((w) => w.symbol === symbol);

  useEffect(() => {
    let c = false; setLoading(true);
    Promise.all([api.getKline(symbol, period), api.getRealtime(symbol).catch(() => null)]).then(([k, r]) => { if (!c) { setKline(k); setRt(r); setLoading(false); } });
    return () => { c = true; };
  }, [symbol, period]);
  useEffect(() => {
    let c = false;
    api.getSymbolOrders(symbol).then((d) => {
      if (!c) {
        const filled = (d.data || []).filter((o: any) => o.status === "FILLED" && o.symbol === symbol);
        setOrders(filled);
      }
    }).catch(() => {});
    return () => { c = true; };
  }, [symbol]);
  useEffect(() => {
    priceRef.current = null;
    setFlash(null);
    const t = setInterval(() => api.getRealtime(symbol).then((d) => {
      const prev = priceRef.current;
      priceRef.current = d.price;
      setRt(d);
      if (prev != null && d.price !== prev) { setFlash(d.price > prev ? "up" : "down"); setTimeout(() => setFlash(null), 700); }
    }).catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [symbol]);

  const option = useMemo(() => (kline ? buildOption(kline, orders) : null), [kline, orders]);

  const pct = rt && rt.prev_close ? ((rt.price - rt.prev_close) / rt.prev_close) * 100 : 0;
  const up = pct >= 0;
  const cur = rt?.price ?? kline?.latest_price;
  const macdSig = signalMeta(kline?.indicators?.macd_signal);
  const trendSig = signalMeta(kline?.indicators?.trend);
  return (
    <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border bg-elevated sticky top-0 z-10">
        <div className={`w-7 h-7 rounded-lg border flex items-center justify-center flex-shrink-0 ${EX_META[symbol.slice(0, 2).toLowerCase()]?.cls || "text-accent bg-accent-soft border-accent/20"}`}>
          <span className="text-[11px] font-bold font-data">{EX_META[symbol.slice(0, 2).toLowerCase()]?.label || symbol.slice(-2)}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-bold text-text-primary truncate">{name}</div>
          <div className="text-[11px] text-text-muted font-data">{symbol}</div>
        </div>
        <div className="flex items-center gap-0.5 p-0.5 bg-input-bg border border-border-strong rounded-lg flex-shrink-0">
          {[{ v: 30, l: "30天" }, { v: 90, l: "90天" }, { v: 180, l: "半年" }, { v: 365, l: "一年" }].map(({ v, l }) => (
            <button key={v} onClick={() => setPeriod(v)}
              className={`px-2 py-1 rounded-md text-[11px] font-medium transition-colors ${period === v ? "bg-elevated text-accent border border-accent/20" : "text-text-muted border border-transparent hover:text-text-secondary"}`}>
              {l}
            </button>
          ))}
        </div>
        <button onClick={() => (watched ? removeFromWatchlist(symbol) : addToWatchlist(symbol, name))}
          className={`flex items-center gap-1 text-[11px] font-medium px-2 py-1.5 rounded-md flex-shrink-0 transition-colors ${watched ? "text-accent bg-accent/10 border border-accent/20" : "text-text-secondary bg-input-bg border border-border-strong hover:text-text-primary hover:border-accent/50"}`}>
          {watched ? <><CheckIcon size={13} />已自选</> : <><PlusIcon size={13} />加自选</>}
        </button>
      </div>

      {loading ? (
        <div className="py-16 flex items-center justify-center text-text-muted text-[12px]"><div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin mr-2" />加载中...</div>
      ) : !kline ? (
        <div className="py-16 text-center text-text-muted text-[12px]">无K线数据</div>
      ) : (
        <>
          {rt && (
            <div className="px-3 pt-3 pb-2.5 border-b border-border">
              <div className="flex items-baseline gap-2.5 mb-2">
                <span className={`text-[24px] font-bold font-data leading-none px-1.5 rounded-md transition-colors duration-150 ${flash === "up" ? "text-up flash-up" : flash === "down" ? "text-down flash-down" : "text-text-primary"}`}>{rt.price.toFixed(2)}</span>
                <span className={`text-[11px] font-data inline-flex items-center gap-0.5 transition-colors duration-150 ${up ? "text-up" : "text-down"}`}>
                  {up ? <ArrowUpIcon size={11} /> : <ArrowDownIcon size={11} />}{up ? "+" : ""}{pct.toFixed(2)}%
                </span>
              </div>
              <div className="grid grid-cols-3 gap-x-1.5 gap-y-1 text-[11px]">
                <Cell l="开盘" v={rt.open?.toFixed(2)} />
                <Cell l="最高" v={rt.high?.toFixed(2)} c="text-up" />
                <Cell l="最低" v={rt.low?.toFixed(2)} c="text-down" />
                <Cell l="昨收" v={rt.prev_close?.toFixed(2)} />
                <Cell l="量" v={rt.volume ? fmtVol(rt.volume) : "-"} />
                <Cell l="换手" v={rt.turnover != null ? `${rt.turnover}%` : "-"} />
              </div>
            </div>
          )}
          <div className="px-1 py-2">
            {option && <ReactECharts option={option} style={{ height: 400 }} notMerge lazyUpdate />}
          </div>
          {kline.indicators && (
            <div className="px-3 pb-4 space-y-2.5">
              <h4 className="section-title">技术指标</h4>
              <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                <div className="bg-input-bg border border-border rounded-lg px-2 py-1.5">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-text-muted">RSI</span>
                    <span className="font-data text-text-secondary">{kline.indicators.rsi != null ? kline.indicators.rsi : "-"}</span>
                  </div>
                  {kline.indicators.rsi != null && (
                    <div className="h-1 rounded-full bg-elevated overflow-hidden">
                      <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(100, Math.max(0, kline.indicators.rsi))}%`, background: kline.indicators.rsi >= 70 ? "#f87171" : kline.indicators.rsi <= 30 ? "#34d399" : "#60a5fa" }} />
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1.5 bg-input-bg border border-border rounded-lg px-2 py-1.5">
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: macdSig.dot }} />
                  <span className="text-text-muted">MACD</span>
                  <span className="font-data ml-auto" style={{ color: macdSig.dot }}>{macdSig.label}</span>
                </div>
                <div className="flex items-center gap-1.5 bg-input-bg border border-border rounded-lg px-2 py-1.5">
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: trendSig.dot }} />
                  <span className="text-text-muted">趋势</span>
                  <span className="font-data ml-auto" style={{ color: trendSig.dot }}>{trendSig.label}</span>
                </div>
                <Ind l="波动率" v={kline.indicators.volatility != null ? `${kline.indicators.volatility}%` : undefined} />
                <Ind l="支撑" v={dedupe(kline.indicators.support)} />
                <Ind l="阻力" v={dedupe(kline.indicators.resistance)} />
              </div>
              {kline.indicators.ma && (
                <>
                  <h4 className="section-title">均线系统</h4>
                  <div className="grid grid-cols-4 gap-1.5 text-[11px]">
                    {Object.entries(kline.indicators.ma).map(([k, v]) => {
                      const color = MA_COLORS[k] || "#a9b4c7";
                      const dist = typeof v === "number" && cur ? ((cur - v) / v) * 100 : null;
                      return (
                        <div key={k} className="text-center bg-input-bg border border-border rounded-lg py-1.5 min-w-0">
                          <div className="text-[11px] text-text-muted">{k.toUpperCase()}</div>
                          <div className="font-data truncate px-1" style={{ color }}>{typeof v === "number" ? v.toFixed(2) : "-"}</div>
                          <div className={`text-[11px] font-data ${dist == null ? "text-text-disabled" : dist >= 0 ? "text-up" : "text-down"}`}>{dist == null ? "—" : `${dist >= 0 ? "+" : ""}${dist.toFixed(1)}%`}</div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
              {kline.indicators.bollinger && (
                <>
                  <h4 className="section-title">布林带</h4>
                  <div className="grid grid-cols-3 gap-1.5 text-[11px]">
                    {Object.entries(kline.indicators.bollinger).map(([k, v]) => (
                      <div key={k} className="text-center bg-input-bg border border-border rounded-lg py-1.5 min-w-0">
                        <div className="text-[11px] text-text-muted">{k === "upper" ? "上轨" : k === "middle" ? "中轨" : "下轨"}</div>
                        <div className="font-data text-text-secondary truncate px-1">{typeof v === "number" ? v.toFixed(2) : "-"}</div>
                      </div>
                    ))}
                  </div>
                  {(() => {
                    const b = kline.indicators.bollinger;
                    if (!b || b.upper == null || b.middle == null || b.lower == null || !cur) return null;
                    const pos = cur >= b.upper ? "价格突破上轨" : cur >= b.middle ? "价格贴近中轨上方" : cur >= b.lower ? "价格贴近中轨下方" : "价格跌破下轨";
                    return <div className="text-[11px] text-text-muted">{pos}</div>;
                  })()}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function buildOption(kline: KlineData, orders: any[]) {
  const grid = "rgba(148,163,184,0.12)", axis = "#64748b";
  const ma = (arr: (number | null)[]) => arr.map((v, i) => (v !== null ? [kline.dates[i], v] : null)).filter(Boolean);
  const buys: any[] = [], sells: any[] = [];
  orders.forEach((o) => {
    const ts = o.traded_at || o.updated_at || o.created_at;
    if (!ts || o.fill_price == null) return;
    const d = ts.slice(0, 10);
    let bi = 0, bd = Infinity;
    kline.dates.forEach((kd, i) => {
      const diff = Math.abs(new Date(kd).getTime() - new Date(d).getTime());
      if (diff < bd) { bd = diff; bi = i; }
    });
    const mark = { coord: [kline.dates[bi], o.fill_price], value: o.side === "BUY" ? "B" : "S" };
    (o.side === "BUY" ? buys : sells).push(mark);
  });
  return {
    backgroundColor: "transparent",
    legend: { data: ["MA5", "MA10", "MA20", "MA60"], textStyle: { color: axis, fontSize: 10 }, top: 0, left: "center", itemWidth: 14, itemHeight: 2, icon: "roundRect" },
    grid: [{ left: "13%", right: "4%", top: "6%", height: "56%" }, { left: "13%", right: "4%", top: "68%", height: "16%" }],
    xAxis: [
      { type: "category", data: kline.dates, gridIndex: 0, axisLine: { lineStyle: { color: grid } }, axisLabel: { show: false }, splitLine: { show: false } },
      { type: "category", data: kline.dates, gridIndex: 1, axisLine: { lineStyle: { color: grid } }, axisLabel: { fontSize: 9, color: axis, formatter: (v: string) => v.slice(5) }, splitLine: { show: false } },
    ],
    yAxis: [
      { type: "value", scale: true, gridIndex: 0, splitLine: { lineStyle: { color: grid } }, axisLabel: { fontSize: 9, color: axis, formatter: (v: number) => v.toFixed(1) } },
      { type: "value", scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { show: false } },
    ],
    tooltip: {
      trigger: "axis", axisPointer: { type: "cross", lineStyle: { color: "#60a5fa" }, crossStyle: { color: "#60a5fa" } },
      backgroundColor: "rgba(15,23,42,0.95)", borderColor: "rgba(148,163,184,0.22)", textStyle: { color: "#f8fafc", fontSize: 11 },
      confine: true,
      formatter: (ps: any[]) => {
        const k = ps.find((p: any) => p.seriesName === "K线"); if (!k) return "";
        const i = k.dataIndex ?? 0;
        const d = kline.data[i] as number[] | undefined;
        if (!d) return "";
        const [o, c, l, h] = d;
        const prevClose = i > 0 ? (kline.data[i - 1]?.[1] ?? o) : o;
        const chg = prevClose > 0 ? ((c - prevClose) / prevClose) * 100 : 0;
        const up = c >= prevClose;
        return `<b>${k.axisValue}</b><br/>开 ${o.toFixed(2)} 高 ${h.toFixed(2)}<br/>低 ${l.toFixed(2)} 收 ${c.toFixed(2)}<br/><span style="color:${up ? "#f87171" : "#34d399"}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span>`;
      },
    },
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], bottom: 2, height: 14, borderColor: grid, backgroundColor: "rgba(15,23,42,0.6)", fillerColor: "rgba(96,165,250,0.12)", handleStyle: { color: "#60a5fa" }, textStyle: { color: axis, fontSize: 9 } },
    ],
    series: [
      { name: "K线", type: "candlestick", data: kline.data, itemStyle: { color: "#f87171", color0: "#34d399", borderColor: "#f87171", borderColor0: "#34d399" } },
      { name: "MA5", type: "line", data: ma(kline.ma5), smooth: true, symbol: "none", lineStyle: { width: 1, color: "#fbbf24" } },
      { name: "MA10", type: "line", data: ma(kline.ma10), smooth: true, symbol: "none", lineStyle: { width: 1, color: "#60a5fa" } },
      { name: "MA20", type: "line", data: ma(kline.ma20), smooth: true, symbol: "none", lineStyle: { width: 1, color: "#a78bfa" } },
      { name: "MA60", type: "line", data: ma(kline.ma60), smooth: true, symbol: "none", lineStyle: { width: 1, color: "#22d3ee" } },
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: kline.volumes, itemStyle: { color: (p: any) => (kline.data[p.dataIndex]?.[1] >= kline.data[p.dataIndex]?.[0] ? "#f87171" : "#34d399") } },
      { name: "买入", type: "scatter", symbol: "pin", symbolSize: 28, data: buys,
        itemStyle: { color: "#ef4444" }, label: { show: true, formatter: "B", position: "bottom", color: "#ef4444", fontSize: 11, fontWeight: "bold", offset: [0, -5] } },
      { name: "卖出", type: "scatter", symbol: "pin", symbolSize: 28, data: sells,
        itemStyle: { color: "#22c55e" }, label: { show: true, formatter: "S", position: "top", color: "#22c55e", fontSize: 11, fontWeight: "bold", offset: [0, 5] } },
    ],
  };
}

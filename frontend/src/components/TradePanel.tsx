import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useChatStore } from "../stores/chatStore";
import { RefreshCwIcon, XIcon, ArrowUpIcon, ArrowDownIcon, PlusIcon, SendIcon, AlertCircleIcon, CheckCircleIcon, ClockIcon } from "./Icon";

const TOKEN = import.meta.env.VITE_TEST_TOKEN || "Bearer mvp_test_token_2026";
const isSH = (s: string) => s.startsWith("60") || s.startsWith("68");
const fmt2 = (v: number) => v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtVol = (v: number) => (v >= 1e8 ? (v / 1e8).toFixed(2) + "亿手" : v >= 1e4 ? (v / 1e4).toFixed(0) + "万手" : v + "手");
function priceStep(price: number): number {
  if (price < 10) return 0.01;
  if (price < 100) return 0.05;
  if (price < 1000) return 0.1;
  return 1;
}
function limitPct(symbol: string): number {
  const code = symbol.replace(/^(sh|sz|bj)/i, "");
  if (/^(300|301|688|689)/.test(code)) return 0.20;
  if (/^(8|4|92)/.test(code)) return 0.30;
  return 0.10;
}
function calcFees(side: string, price: number, qty: number, symbol: string) {
  const amount = price * qty;
  const commission = Math.max(amount * 0.00025, 5);
  const stamp = side === "SELL" ? amount * 0.0005 : 0;
  const transfer = isSH(symbol) ? amount * 0.00001 : 0;
  const total = commission + stamp + transfer;
  return { commission, stamp, transfer, total, all: amount + total };
}
const Minus = ({ size = 15 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round"><line x1="5" y1="12" x2="19" y2="12" /></svg>
);
const Cell = ({ l, v, c }: { l: string; v?: string; c?: string }) => (
  <div className="flex items-baseline justify-between gap-1 bg-input-bg/60 rounded-md px-1.5 py-1 min-w-0">
    <span className="text-text-muted flex-shrink-0">{l}</span>
    <span className={`font-data truncate ${c || "text-text-secondary"}`} title={v}>{v ?? "-"}</span>
  </div>
);

const ORDER_STATUS_CN: Record<string, { label: string; cls: string }> = {
  FILLED: { label: "已成交", cls: "text-up border-up/20 bg-up-soft" },
  PARTIALLY_FILLED: { label: "部分成交", cls: "text-accent border-accent/20 bg-accent/10" },
  CANCELLED: { label: "已撤单", cls: "text-text-muted border-border bg-input-bg" },
  REJECTED: { label: "已拒绝", cls: "text-danger border-danger/20 bg-danger-soft" },
};

function fmtOrderTime(s?: string): string {
  if (!s) return "-";
  const d = new Date(s.replace(" ", "T"));
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}


export function OrdersList() {
  const { activeOrders, cancelOrder, loadActiveOrders, actionPending } = useChatStore();
  useEffect(() => { loadActiveOrders(); }, [loadActiveOrders]);
  return (
    <div>
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between sticky top-0 bg-bg-elevated z-10">
        <span className="section-title">活跃委托 ({activeOrders.length})</span>
        <button onClick={loadActiveOrders} className="icon-btn w-6 h-6"><RefreshCwIcon size={12} /></button>
      </div>
      {activeOrders.length === 0 ? (
        <div className="px-4 py-8 text-center flex flex-col items-center gap-2">
          <div className="w-9 h-9 rounded-full bg-input-bg border border-border flex items-center justify-center text-text-muted"><ClockIcon size={16} /></div>
          <div className="text-[11px] text-text-muted">暂无活跃委托</div>
          <div className="text-[11px] text-text-disabled">下单后会在此显示委托状态</div>
        </div>
      ) : activeOrders.map((o: any) => {
        const buy = o.side === "BUY";
        return (
          <div key={o.order_id} className="px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-hover/30 transition-colors">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`chip ${buy ? "bg-up-soft text-up border-up/20" : "bg-down-soft text-down border-down/20"}`}>{buy ? "买" : "卖"}</span>
                <span className="text-[12px] text-text-primary truncate">{o.name || o.symbol}</span>
                <span className="text-[11px] text-text-muted font-data flex-shrink-0">{o.symbol}</span>
              </div>
              {(o.status !== "filled" && o.status !== "FILLED" && o.status !== "cancelled") && (
                <button onClick={() => cancelOrder(o.order_id)} disabled={actionPending} className="btn px-2 py-1 text-[11px] text-danger border border-danger/20 hover:bg-danger/10 rounded-md flex-shrink-0 disabled:opacity-50"><XIcon size={11} />撤单</button>
              )}
            </div>
            <div className="flex items-center gap-2.5 mt-1 text-[11px] text-text-muted font-data">
              <span>{o.quantity}股</span>{o.price > 0 && <span>@{o.price}</span>}
              <span>{o.order_type === "MARKET" ? "市价" : "限价"}</span>
              {o.filled_qty > 0 && <span className="text-accent">已成交{o.filled_qty}股</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function HistoryOrdersList() {
  const { orderHistory, loadOrderHistory } = useChatStore();
  useEffect(() => { loadOrderHistory(); }, [loadOrderHistory]);
  return (
    <div>
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between sticky top-0 bg-bg-elevated z-10">
        <span className="section-title">历史记录 ({orderHistory.length})</span>
        <button onClick={loadOrderHistory} className="icon-btn w-6 h-6"><RefreshCwIcon size={12} /></button>
      </div>
      {orderHistory.length === 0 ? (
        <div className="px-4 py-8 text-center flex flex-col items-center gap-2">
          <div className="w-9 h-9 rounded-full bg-input-bg border border-border flex items-center justify-center text-text-muted"><ClockIcon size={16} /></div>
          <div className="text-[11px] text-text-muted">暂无历史记录</div>
          <div className="text-[11px] text-text-disabled">已成交 / 已撤单 / 已拒绝的委托会在此显示</div>
        </div>
      ) : orderHistory.map((o: any) => {
        const buy = o.side === "BUY";
        const st = ORDER_STATUS_CN[o.status] || { label: o.status || "-", cls: "text-text-muted border-border bg-input-bg" };
        const fillPrice = o.fill_price ?? o.avg_fill_price ?? null;
        const typeLabel = o.order_type === "MARKET" ? "市价" : o.order_type === "AUCTION" ? "竞价" : o.order_type === "ESTIMATED" ? "预估" : "限价";
        return (
          <div key={o.order_id} className="px-4 py-2.5 border-b border-border last:border-b-0 hover:bg-hover/30 transition-colors">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className={`chip ${buy ? "bg-up-soft text-up border-up/20" : "bg-down-soft text-down border-down/20"}`}>{buy ? "买" : "卖"}</span>
                <span className="text-[12px] text-text-primary truncate">{o.name || o.symbol}</span>
                <span className="text-[11px] text-text-muted font-data flex-shrink-0">{o.symbol}</span>
              </div>
              <span className={`chip ${st.cls} flex-shrink-0`}>{st.label}</span>
            </div>
            <div className="flex items-center gap-2.5 mt-1 text-[11px] text-text-muted font-data">
              <span>{o.quantity}股</span>{o.price > 0 && <span>@{o.price}</span>}
              <span>{typeLabel}</span>
              {o.filled_qty > 0 && <span className="text-accent">成交{o.filled_qty}股{fillPrice ? ` @${fillPrice}` : ""}</span>}
            </div>
            {o.side === "SELL" && o.realized_pnl != null && (
              <div className="flex items-center gap-2 mt-0.5 text-[11px] font-data">
                <span className="text-text-muted">卖出收益</span>
                <span className={Number(o.realized_pnl) >= 0 ? "text-up" : "text-down"}>
                  {Number(o.realized_pnl) >= 0 ? "+" : ""}{Number(o.realized_pnl).toFixed(2)} 元
                </span>
              </div>
            )}
            <div className="flex items-center justify-between gap-2 mt-0.5 text-[11px] text-text-disabled">
              <span>{fmtOrderTime(o.created_at)}</span>
              {o.cancel_reason && <span className="truncate">{o.cancel_reason}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function TradePanel({ symbol, name }: { symbol: string; name: string }) {
  const [stockInfo, setStockInfo] = useState<any>(null);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [orderType, setOrderType] = useState<"LIMIT" | "MARKET">("LIMIT");
  const [quantity, setQuantity] = useState(100);
  const [price, setPrice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState<{ t: string; ok: boolean } | null>(null);
  const liveRef = useRef<number | null>(null);
  const { portfolio, loadActiveOrders, loadOrderHistory } = useChatStore();

  const currentPrice = livePrice ?? stockInfo?.price ?? 0;
  const prevClose = stockInfo?.prev_close ?? 0;
  const chg = currentPrice - prevClose;
  const chgPct = prevClose > 0 ? (chg / prevClose) * 100 : 0;
  const numPrice = parseFloat(price) || currentPrice || 0;
  const fees = useMemo(() => calcFees(side, numPrice, quantity, symbol), [side, numPrice, quantity, symbol]);
  const balance = portfolio?.balance ?? 0;
  const sellable = portfolio?.positions.find((p) => p.symbol === symbol)?.sellable_quantity ?? 0;
  const step = priceStep(numPrice || currentPrice || 0);
  const stepCents = Math.round(step * 100);
  const buyAmount = numPrice * quantity;
  const insufficient = side === "BUY" && numPrice > 0 && quantity >= 100 && buyAmount > balance;
  const limitPctVal = limitPct(symbol);
  const limitUp = prevClose > 0 ? +(prevClose * (1 + limitPctVal)).toFixed(2) : 0;
  const limitDown = prevClose > 0 ? +(prevClose * (1 - limitPctVal)).toFixed(2) : 0;
  const priceOutOfRange = orderType === "LIMIT" && numPrice > 0 && prevClose > 0 && (numPrice > limitUp || numPrice < limitDown);

  useEffect(() => { setPrice(""); setStockInfo(null); liveRef.current = null; setLivePrice(null); }, [symbol]);
  useEffect(() => { api.getRealtime(symbol).then((d) => { setStockInfo(d); if (!liveRef.current) { liveRef.current = d.price; setLivePrice(d.price); } }).catch(() => {}); }, [symbol]);

  useEffect(() => {
    const es = new EventSource(`/api/market/price-stream?symbols=${encodeURIComponent(symbol)}&token=${encodeURIComponent(TOKEN)}`);
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.type === "price_update" && Array.isArray(d.prices)) {
          for (const p of d.prices) {
            if (p.symbol !== symbol || !p.price) continue;
            const np = parseFloat(p.price); const prev = liveRef.current;
            liveRef.current = np; setLivePrice(np);
            if (prev && np !== prev) { setFlash(np > prev ? "up" : "down"); setTimeout(() => setFlash(null), 700); }
          }
        }
      } catch {}
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [symbol]);

  useEffect(() => { if (orderType === "LIMIT" && !price && currentPrice > 0) setPrice(currentPrice.toFixed(2)); }, [orderType, currentPrice, price]);

  const setRatio = (r: number) => {
    if (side === "BUY" && numPrice > 0) setQuantity(Math.max(100, Math.floor((balance * r) / numPrice / 100) * 100));
    else if (side === "SELL") setQuantity(Math.max(100, Math.floor((sellable * r) / 100) * 100));
  };

  const submit = useCallback(async () => {
    const qty = Math.max(100, Math.floor(quantity / 100) * 100);
    setQuantity(qty);
    if (orderType === "LIMIT" && numPrice <= 0) { setMsg({ t: "请输入有效价格", ok: false }); return; }
    setSubmitting(true); setMsg(null);
    try {
      const res = await api.placeOrder(symbol, name, side, orderType, qty, orderType === "LIMIT" ? numPrice : undefined);
      setMsg({ t: res.message || (res.success ? "委托已提交" : "委托失败"), ok: !!res.success });
      if (res.success) { loadActiveOrders(); loadOrderHistory(); }
    } catch (e: any) { setMsg({ t: e?.message || "网络异常", ok: false }); }
    finally { setSubmitting(false); }
  }, [symbol, name, side, orderType, quantity, numPrice, loadActiveOrders, loadOrderHistory]);

  const isUp = chg >= 0;
  return (
    <div className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden p-3 space-y-3">
      {stockInfo && (
        <div className="card p-3.5">
          <div className="flex items-start justify-between gap-2 mb-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-[14px] font-bold text-text-primary truncate">{name}</span>
                <span className="chip text-text-muted border-border">{isSH(symbol) ? "沪" : "深"}</span>
              </div>
              <div className="text-[11px] text-text-muted font-data mt-0.5">{symbol}</div>
            </div>
            <div className="text-right flex-shrink-0">
              <div className={`text-[22px] leading-tight font-bold font-data px-1.5 rounded-md transition-colors duration-150 ${flash === "up" ? "text-up flash-up" : flash === "down" ? "text-down flash-down" : "text-text-primary"}`}>{currentPrice.toFixed(2)}</div>
              <div className={`text-[11px] font-data inline-flex items-center gap-1 transition-colors duration-150 ${isUp ? "text-up" : "text-down"}`}>
                {isUp ? <ArrowUpIcon size={10} /> : <ArrowDownIcon size={10} />}{isUp ? "+" : ""}{chg.toFixed(2)} ({isUp ? "+" : ""}{chgPct.toFixed(2)}%)
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-1 text-[11px]">
            <Cell l="昨收" v={prevClose.toFixed(2)} />
            <Cell l="今开" v={stockInfo.open.toFixed(2)} />
            <Cell l="量" v={fmtVol(stockInfo.volume)} />
            <Cell l="最高" v={stockInfo.high.toFixed(2)} c="text-up" />
            <Cell l="最低" v={stockInfo.low.toFixed(2)} c="text-down" />
            <Cell l="换手" v={stockInfo.turnover != null ? stockInfo.turnover + "%" : "-"} />
          </div>
        </div>
      )}

      <div className="card p-3.5 space-y-3">
        <div className="section-title">委托下单</div>
        <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-input-bg border border-border">
          <button onClick={() => { setSide("BUY"); setPrice(""); }}
            className={`py-2 rounded-lg text-[13px] font-bold border transition-all ${side === "BUY" ? "bg-up/15 text-up border-up/30" : "text-text-muted border-transparent hover:text-text-secondary"}`}>买入</button>
          <button onClick={() => { setSide("SELL"); setPrice(""); }}
            className={`py-2 rounded-lg text-[13px] font-bold border transition-all ${side === "SELL" ? "bg-down/15 text-down border-down/30" : "text-text-muted border-transparent hover:text-text-secondary"}`}>卖出</button>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {(["LIMIT", "MARKET"] as const).map((t) => (
            <button key={t} onClick={() => setOrderType(t)}
              className={`py-1.5 text-[12px] font-medium rounded-lg border transition-colors ${orderType === t ? "bg-accent/15 text-accent border-accent/30" : "bg-input-bg text-text-muted border-border-strong hover:text-text-secondary"}`}>
              {t === "LIMIT" ? "限价" : "市价"}
            </button>
          ))}
        </div>
        {orderType === "MARKET" && (
          <div className="flex items-center gap-1.5 text-[11px] text-text-muted"><ClockIcon size={11} />将以市价即时成交，价格由系统撮合确定</div>
        )}
        <div>
          <div className="flex items-baseline justify-between mb-1.5">
            <label className="text-[11px] text-text-muted">数量（股）</label>
            <span className="text-[11px] text-text-muted font-data">可用 {side === "BUY" ? `¥${fmt2(balance)}` : `${sellable}股`}</span>
          </div>
          <div className="grid grid-cols-[36px_1fr_36px] gap-1.5">
            <button onClick={() => setQuantity((q) => Math.max(100, q - 100))} className="btn h-9 rounded-lg bg-input-bg border border-border-strong text-text-secondary hover:bg-elevated"><Minus /></button>
            <input type="number" step={100} min={100} value={quantity || ""} onChange={(e) => setQuantity(parseInt(e.target.value) || 0)}
              onBlur={() => setQuantity((q) => Math.max(100, Math.floor(q / 100) * 100))} className="input input-num h-9" />
            <button onClick={() => setQuantity((q) => q + 100)} className="btn h-9 rounded-lg bg-input-bg border border-border-strong text-text-secondary hover:bg-elevated"><PlusIcon size={15} /></button>
          </div>
          <div className="grid grid-cols-4 divide-x divide-border border border-border-strong rounded-lg overflow-hidden mt-1.5">
            {[0.25, 0.5, 0.75, 1].map((r) => (
              <button key={r} onClick={() => setRatio(r)} className="py-1.5 text-[11px] font-medium text-text-muted hover:bg-hover hover:text-accent transition-colors">
                {r === 1 ? "全仓" : `${r * 100}%`}
              </button>
            ))}
          </div>
          <p className="text-[11px] text-text-muted mt-1">100股整数倍（1手 = 100股）</p>
        </div>
        {orderType === "LIMIT" && (
          <div>
            <label className="text-[11px] text-text-muted block mb-1.5">{side === "BUY" ? "买入" : "卖出"}价格（元）</label>
            <div className="grid grid-cols-[1fr_auto_auto_auto] gap-1.5">
              <input type="number" step={step} value={price} onChange={(e) => setPrice(e.target.value)} placeholder={currentPrice ? currentPrice.toFixed(2) : "0.00"} className="input input-num h-9" />
              <button onClick={() => setPrice((p) => ((Math.round((parseFloat(p) || currentPrice || 0) * 100) - stepCents) / 100).toFixed(2))} className="btn px-2 h-9 text-[11px] bg-input-bg border border-border-strong text-text-secondary hover:bg-elevated rounded-lg">-{step >= 1 ? step.toFixed(0) : step.toFixed(2)}</button>
              <button onClick={() => currentPrice > 0 && setPrice(currentPrice.toFixed(2))} className="btn px-2.5 h-9 text-[11px] bg-accent-glow border border-accent/20 text-accent hover:bg-hover rounded-lg">现价</button>
              <button onClick={() => setPrice((p) => ((Math.round((parseFloat(p) || currentPrice || 0) * 100) + stepCents) / 100).toFixed(2))} className="btn px-2 h-9 text-[11px] bg-input-bg border border-border-strong text-text-secondary hover:bg-elevated rounded-lg">+{step >= 1 ? step.toFixed(0) : step.toFixed(2)}</button>
            </div>
          </div>
        )}
        {numPrice > 0 && quantity > 0 && (
          <div className="rounded-lg bg-input-bg/70 border border-border px-3 py-2.5 space-y-1 text-[11px]">
            <div className="section-title mb-1" style={{ fontSize: 11 }}>费用预估</div>
            {[["成交金额", `¥${fmt2(numPrice * quantity)}`], ["佣金 (万2.5)", `¥${fees.commission.toFixed(2)}`], ["印花税", side === "SELL" ? `¥${fees.stamp.toFixed(2)}` : "免（买入）"], ["过户费", isSH(symbol) ? `¥${fees.transfer.toFixed(2)}` : "免"]].map(([l, v]) => (
              <div key={l} className="flex justify-between gap-2"><span className="text-text-muted flex-shrink-0">{l}</span><span className="font-data text-text-secondary truncate">{v}</span></div>
            ))}
            <div className="border-t border-border my-1" />
            <div className="flex justify-between gap-2"><span className="text-text-primary font-medium">合计费用</span><span className="font-data text-accent font-semibold">¥{fees.total.toFixed(2)}</span></div>
            <div className="flex justify-between gap-2"><span className="text-text-muted">预估总支出</span><span className="font-data text-accent font-bold">¥{fmt2(fees.all)}</span></div>
          </div>
        )}
        {msg && (
          <div className={`flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] border ${msg.ok ? "bg-success-soft text-success border-success/20" : "bg-danger-soft text-danger border-danger/20"}`}>
            {msg.ok ? <CheckCircleIcon size={13} /> : <AlertCircleIcon size={13} />}{msg.t}
          </div>
        )}
        {priceOutOfRange && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-[11px] bg-danger-soft text-danger border-danger/20">
            <AlertCircleIcon size={13} />价格超出涨跌停范围（{limitDown.toFixed(2)} ~ {limitUp.toFixed(2)}）
          </div>
        )}
        {side === "BUY" && numPrice > 0 && quantity >= 100 && (
          <div className={`flex items-center justify-between gap-2 px-3 py-2 rounded-lg border text-[11px] ${insufficient ? "bg-danger-soft text-danger border-danger/20" : "bg-input-bg/60 border-border text-text-muted"}`}>
            {insufficient ? (
              <span className="flex items-center gap-1.5"><AlertCircleIcon size={12} />资金不足，可用 ¥{fmt2(balance)}</span>
            ) : (
              <span>将使用可用资金 <span className="font-data text-text-secondary">{(balance > 0 ? (buyAmount / balance) * 100 : 0).toFixed(1)}%</span></span>
            )}
          </div>
        )}
        <div className="px-4">
          <button onClick={submit} disabled={submitting || quantity < 100 || insufficient || priceOutOfRange}
            className={`btn w-full h-7 text-[11px] font-bold text-white ${side === "BUY" ? "bg-up hover:bg-up/90" : "bg-down hover:bg-down/90"}`}>
            {submitting ? <span className="btn-spinner" /> : <SendIcon size={10} />}
            {submitting ? "提交中..." : `${side === "BUY" ? "确认买入" : "确认卖出"} ${quantity}股 ${name}`}
          </button>
        </div>
      </div>

      <div className="card overflow-hidden"><OrdersList /></div>

      <div className="card overflow-hidden"><HistoryOrdersList /></div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { ClockIcon, ArrowUpIcon, ArrowDownIcon, AlertCircleIcon } from "./Icon";
import { useChatStore } from "../stores/chatStore";

const TOKEN = import.meta.env.VITE_TEST_TOKEN || "Bearer mvp_test_token_2026";

interface Props {
  tradePlan: any;
  onConfirm: (price: number, quantity: number, forceMode: boolean) => void;
  onCancel: () => void;
}

export default function TradeConfirmPanel({ tradePlan, onConfirm, onCancel }: Props) {
  const actionPending = useChatStore((s) => s.actionPending);
  const [live, setLive] = useState(tradePlan.current_price);
  const [prev, setPrev] = useState(tradePlan.current_price);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const [countdown, setCountdown] = useState(60);
  const [expired, setExpired] = useState(false);
  const [force, setForce] = useState(false);
  const liveRef = useRef(tradePlan.current_price);

  // 可编辑的委托价/股数（默认取 Agent 建议值，用户可修改）
  const [priceInput, setPriceInput] = useState(String(tradePlan.suggested_price));
  const [qtyInput, setQtyInput] = useState(String(tradePlan.suggested_quantity));

  const price = parseFloat(priceInput);
  const quantity = parseInt(qtyInput, 10);
  const validPrice = Number.isFinite(price) && price > 0 ? price : tradePlan.suggested_price;
  const validQuantity = Number.isFinite(quantity) && quantity > 0 ? quantity : tradePlan.suggested_quantity;
  const amount = validPrice * validQuantity;

  useEffect(() => {
    const es = new EventSource(`/api/market/price-stream?symbols=${encodeURIComponent(tradePlan.symbol)}&token=${encodeURIComponent(TOKEN)}`);
    es.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d.type === "price_update" && Array.isArray(d.prices)) {
          for (const p of d.prices) {
            if (p.symbol !== tradePlan.symbol || !p.price) continue;
            const np = parseFloat(p.price); const pv = liveRef.current;
            liveRef.current = np; setPrev(pv); setLive(np);
            if (np !== pv) { setFlash(np > pv ? "up" : "down"); setTimeout(() => setFlash(null), 700); }
          }
        }
      } catch {}
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [tradePlan.symbol]);

  useEffect(() => {
    if (!tradePlan.is_trading_time) return;
    const t = setInterval(() => setCountdown((c) => {
      if (c <= 1) { clearInterval(t); setExpired(true); if (force) onConfirm(liveRef.current || validPrice, validQuantity, true); return 0; }
      return c - 1;
    }), 1000);
    return () => clearInterval(t);
  }, [tradePlan.is_trading_time, force, validPrice, validQuantity, onConfirm]);

  const isBuy = tradePlan.side === "BUY";
  const chg = live - prev; const pct = prev > 0 ? (chg / prev) * 100 : 0; const up = chg >= 0;
  const risk = tradePlan.risk_level;
  const riskCls = risk === "CRITICAL" ? "bg-danger-soft text-danger border-danger/20" : risk === "HIGH" ? "bg-warning-soft text-warning border-warning/20" : "bg-success-soft text-success border-success/20";

  return (
    <div className="rounded-xl border border-border bg-surface-secondary shadow-2xl overflow-hidden animate-scale-in">
      {/* 头部 */}
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 bg-elevated border-b border-border flex-wrap">
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <span className="text-[13px] font-bold text-text-primary truncate">{tradePlan.name}({tradePlan.symbol})</span>
          <span className={`chip ${isBuy ? "bg-up-soft text-up border-up/20" : "bg-down-soft text-down border-down/20"}`}>{isBuy ? "买入" : "卖出"}</span>
          <span className={`chip ${riskCls}`}>{risk === "CRITICAL" ? "极高风险" : risk === "HIGH" ? "高风险" : "风险正常"}</span>
        </div>
        {tradePlan.is_trading_time && (
          <div className="flex items-center gap-1.5">
            <ClockIcon size={13} className={countdown <= 10 ? "text-danger" : "text-text-muted"} />
            <span className={`text-[12px] font-data ${countdown <= 10 ? "text-danger animate-pulse" : "text-text-secondary"}`}>{expired ? "已超时" : `${countdown}s`}</span>
          </div>
        )}
      </div>
      {/* 实时报价 */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-bg border-b border-border">
        <span className="text-[11px] text-text-muted uppercase tracking-wider">实时报价</span>
        <div className="flex items-baseline gap-2">
          <span className={`text-[20px] font-bold font-data ${flash === "up" ? "text-up" : flash === "down" ? "text-down" : "text-text-primary"}`}>¥{live.toFixed(2)}</span>
          <span className={`text-[11px] font-data inline-flex items-center gap-0.5 ${up ? "text-up" : "text-down"}`}>
            {up ? <ArrowUpIcon size={10} /> : <ArrowDownIcon size={10} />}{Math.abs(chg).toFixed(2)} ({up ? "+" : ""}{pct.toFixed(2)}%)
          </span>
        </div>
      </div>
      {/* 委托方案摘要 */}
      <div className="p-4 space-y-2.5">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-input-bg border border-border rounded-lg px-2 py-2">
            <div className="text-[11px] text-text-muted">委托价</div>
            <div className="flex items-center justify-center gap-0.5 font-data text-[14px] font-bold text-text-primary">
              <span className="text-text-muted">¥</span>
              <input type="number" value={priceInput} step="0.01" min="0.01"
                onChange={(e) => setPriceInput(e.target.value)}
                className="w-full min-w-0 bg-transparent text-center font-data text-[14px] font-bold text-text-primary outline-none" />
            </div>
          </div>
          <div className="bg-input-bg border border-border rounded-lg px-2 py-2">
            <div className="text-[11px] text-text-muted">股数</div>
            <input type="number" value={qtyInput} step="100" min="100"
              onChange={(e) => setQtyInput(e.target.value)}
              className="w-full min-w-0 bg-transparent text-center font-data text-[14px] font-bold text-text-primary outline-none" />
          </div>
          <div className="bg-input-bg border border-border rounded-lg px-2 py-2">
            <div className="text-[11px] text-text-muted">预估金额</div>
            <div className="font-data text-[14px] font-bold text-text-primary">¥{amount.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</div>
          </div>
        </div>
        {tradePlan.smart_pricing_note && (
          <p className="text-[11px] text-accent bg-accent-soft/40 border border-accent/15 rounded-lg px-3 py-2 leading-relaxed">{tradePlan.smart_pricing_note}</p>
        )}
        {tradePlan.force_mode_available && (
          <label className="flex items-start gap-2.5 cursor-pointer select-none rounded-lg bg-warning-soft/60 border border-warning/20 px-3 py-2.5">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="w-3.5 h-3.5 mt-0.5 accent-warning" />
            <div>
              <span className="text-[12px] font-medium text-warning">本轮对话自动执行，无需逐一确认</span>
              {force && <p className="text-[11px] text-warning/80 mt-0.5 flex items-center gap-1"><AlertCircleIcon size={11} />超时60秒也将自动提交</p>}
            </div>
          </label>
        )}
      </div>
      {/* 操作按钮 */}
      <div className="flex items-center justify-center gap-2 p-3 bg-elevated border-t border-border">
        <button onClick={onCancel} disabled={actionPending} className="btn px-5 h-8 text-[12px] text-text-secondary bg-input-bg border border-input-border hover:bg-elevated-hover disabled:opacity-50">取消</button>
        <button onClick={() => onConfirm(validPrice, validQuantity, force)} disabled={expired || actionPending}
          className={`btn px-5 h-8 text-[12px] font-bold text-white ${isBuy ? "bg-up hover:bg-up/90" : "bg-down hover:bg-down/90"}`}>
          {actionPending ? <span className="btn-spinner" /> : null}{expired ? "已超时" : isBuy ? "确认买入" : "确认卖出"}
        </button>
      </div>
    </div>
  );
}

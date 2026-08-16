import { useEffect, useRef, useState } from "react";
import { useChatStore } from "../stores/chatStore";
import { api } from "../api/client";
import AddWatchlistDialog from "./AddWatchlistDialog";
import { LayersIcon, PlusIcon, XIcon, LayoutDashboardIcon, WalletIcon, TrendingUpIcon, ArrowUpIcon, ArrowDownIcon, EyeIcon, EyeOffIcon } from "./Icon";

const fmt2 = (v: number) => v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const toBeijing = (s: string) => { try { return new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z").toLocaleDateString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }); } catch { return s; } };
const Pct = ({ v, size = 10 }: { v: number; size?: number }) => (
  <span className={`font-data inline-flex items-center gap-0.5 ${v > 0 ? "text-up" : v < 0 ? "text-down" : "text-text-muted"}`}>
    {v > 0 && <ArrowUpIcon size={size} />}{v < 0 && <ArrowDownIcon size={size} />}
    {v !== 0 && `${v > 0 ? "+" : ""}${v.toFixed(2)}%`}
    {v === 0 && "-"}
  </span>
);

export default function Sidebar() {
  const { watchlist, portfolio, activeSymbol, conversations, conversationId, loadWatchlist, loadPortfolio, loadActiveOrders, loadConversations, sendMessage, loadConversation, newConversation, openStockDetail, removeFromWatchlist } = useChatStore();
  const [addOpen, setAddOpen] = useState(false);
  const [maskAssets, setMaskAssets] = useState(false);
  const [now, setNow] = useState(new Date());
  const priceRef = useRef<Record<string, number>>({});
  const [flashMap, setFlashMap] = useState<Record<string, "up" | "down">>({});

  useEffect(() => {
    const c = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(c);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "n") { e.preventDefault(); newConversation(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newConversation]);

  const money = (v: number) => (maskAssets ? "••••" : fmt2(v));

  useEffect(() => { loadWatchlist(); loadPortfolio(); loadActiveOrders(); loadConversations(); }, []);
  useEffect(() => {
    const t = setInterval(() => { loadWatchlist(); loadPortfolio(); loadActiveOrders(); }, 3000);
    return () => clearInterval(t);
  }, [loadWatchlist, loadPortfolio, loadActiveOrders]);

  useEffect(() => {
    const next: Record<string, "up" | "down"> = {};
    for (const w of watchlist) {
      const prev = priceRef.current[w.symbol];
      if (prev != null && typeof w.price === "number" && w.price !== prev) {
        next[w.symbol] = w.price > prev ? "up" : "down";
      }
      if (typeof w.price === "number") priceRef.current[w.symbol] = w.price;
    }
    if (Object.keys(next).length) { setFlashMap(next); setTimeout(() => setFlashMap({}), 700); }
  }, [watchlist]);

  return (
    <aside className="w-[240px] xl:w-[280px] h-full glass border-r border-border-strong flex flex-col flex-shrink-0 overflow-hidden">
      <div className="px-4 h-12 border-b border-border flex items-center gap-2.5 flex-shrink-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent/20 to-accent/5 border border-accent/20 flex items-center justify-center text-accent flex-shrink-0"><LayersIcon size={16} /></div>
        <h1 className="flex-1 min-w-0 text-[13px] font-bold text-text-primary tracking-tight truncate">PaperTradeAgent</h1>
        <span className="font-data text-[12px] font-semibold text-accent tabular-nums whitespace-nowrap flex-shrink-0">{now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
      </div>

      <div className="px-3 pt-3 pb-2 flex-shrink-0">
        <button onClick={newConversation} className="btn btn-secondary w-full py-3 text-[13px] card-lift" title="新建会话 (Ctrl+N)">
          <PlusIcon size={16} />新建会话
          <span className="ml-auto text-[11px] text-text-muted font-mono border border-border rounded px-1.5 py-0.5">Ctrl N</span>
        </button>
      </div>

      <div className="px-3 pb-3 flex-shrink-0">
        <h2 className="section-title px-1 mb-1.5">历史会话</h2>
        <div className="space-y-1 pr-0.5">
          {conversations.slice(0, 5).map((c) => (
            <div key={c.id} onClick={() => loadConversation(c.id)}
              className={`group flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-colors ${conversationId === c.id ? "bg-accent-soft text-accent border border-accent/20" : "border border-transparent text-text-secondary hover:bg-hover hover:text-text-primary"}`}>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-medium truncate">{c.title || "新会话"}</div>
                <div className="text-[11px] text-text-muted mt-0.5">{toBeijing(c.updated_at)}</div>
              </div>
              <button onClick={async (e) => { e.stopPropagation(); await api.deleteConversation(c.id); loadConversations(); }}
                className="icon-btn w-6 h-6 rounded-md opacity-0 group-hover:opacity-100 hover:text-danger"><XIcon size={11} /></button>
            </div>
          ))}
          {Array.from({ length: Math.max(0, 5 - conversations.length) }).map((_, i) => (
            <div key={`empty-${i}`} className="flex items-center px-2.5 h-[40px] rounded-lg border border-dashed border-border/70">
              {i === 0 && <span className="text-[11px] text-text-disabled">暂无历史会话</span>}
            </div>
          ))}
        </div>
      </div>

        <div className="px-3 py-3 border-b border-border flex-shrink-0">
          <h2 className="section-title px-1 mb-1.5">快捷操作</h2>
          <div className="space-y-0.5">
            <button onClick={() => sendMessage("查看持仓")} className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-text-secondary hover:bg-hover hover:text-text-primary transition-colors">
              <LayoutDashboardIcon size={15} className="text-accent/70 flex-shrink-0" />持仓概览
            </button>
            <button onClick={() => setAddOpen(true)} className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-text-secondary hover:bg-hover hover:text-text-primary transition-colors">
              <TrendingUpIcon size={15} className="text-success/70 flex-shrink-0" />添加自选
            </button>
          </div>
        </div>

        <div className="px-3 py-3 border-b border-border flex-shrink-0">
          <h2 className="section-title px-1 mb-1.5">自选股 ({watchlist.length})</h2>
          {watchlist.length === 0 ? (
            <div className="h-[256px] rounded-lg border border-dashed border-border flex flex-col items-center justify-center gap-1.5">
              <TrendingUpIcon size={16} className="text-text-muted" />
              <div className="text-[11px] text-text-muted">尚未添加自选股</div>
              <div className="text-[11px] text-text-disabled">点击「添加自选」开始关注</div>
            </div>
          ) : (
            <div className="h-[256px] overflow-y-auto space-y-1 pr-0.5">
              {watchlist.map((w) => (
                <div key={w.symbol} onClick={() => openStockDetail(w.symbol, w.name)}
                  className={`group flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer border transition-colors ${activeSymbol === w.symbol ? "bg-accent-soft border-accent/20" : "border-transparent hover:bg-hover"}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] font-medium text-text-primary truncate">{w.name}</span>
                      {typeof w.price === "number" && w.price > 0 && <span className={`font-data text-[12px] font-semibold flex-shrink-0 transition-colors duration-150 ${flashMap[w.symbol] === "up" ? "text-up" : flashMap[w.symbol] === "down" ? "text-down" : "text-text-primary"}`}>{w.price.toFixed(2)}</span>}
                    </div>
                    <div className="flex items-baseline justify-between gap-2 mt-0.5">
                      <span className="text-[11px] text-text-muted font-data">{w.symbol}</span>
                      {typeof w.change_pct === "number" && <span className="text-[11px]"><Pct v={w.change_pct} /></span>}
                    </div>
                  </div>
                  <button onClick={(e) => { e.stopPropagation(); if (confirm(`移除 ${w.name}？`)) removeFromWatchlist(w.symbol); }}
                    className="icon-btn w-6 h-6 rounded-md opacity-0 group-hover:opacity-100 hover:text-danger"><XIcon size={11} /></button>
                </div>
              ))}
            </div>
          )}
        </div>

        {portfolio && (
          <div className="px-3 py-3 flex-1 min-h-0 flex flex-col">
            <h2 className="section-title px-1 mb-1.5 flex items-center gap-1.5 flex-shrink-0"><WalletIcon size={12} />账户资产</h2>
            <div className="card p-3 space-y-2.5 flex-shrink-0">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-[11px] text-text-muted">总资产</div>
                  <div className="font-data text-[17px] font-bold text-text-primary leading-tight truncate">{money(portfolio.total_assets)}</div>
                </div>
                <button onClick={() => setMaskAssets((v) => !v)} className="icon-btn w-6 h-6 rounded-md flex-shrink-0" title={maskAssets ? "显示金额" : "隐藏金额"}>
                  {maskAssets ? <EyeOffIcon size={13} /> : <EyeIcon size={13} />}
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="min-w-0"><div className="text-[11px] text-text-muted">可用资金</div><div className="font-data text-[11px] text-text-secondary truncate">{money(portfolio.balance)}</div></div>
                <div className="min-w-0"><div className="text-[11px] text-text-muted">持仓市值</div><div className="font-data text-[11px] text-text-secondary truncate">{money(portfolio.total_market_value)}</div></div>
              </div>
              <div className="border-t border-border pt-2 flex items-center justify-between gap-2">
                <span className="text-[11px] text-text-muted flex-shrink-0">累计盈亏</span>
                <span className={`font-data text-[11px] font-semibold inline-flex items-center gap-1 flex-shrink-0 ${portfolio.total_pnl >= 0 ? "text-up" : "text-down"}`}>
                  {portfolio.total_pnl >= 0 ? <ArrowUpIcon size={10} /> : <ArrowDownIcon size={10} />}
                  {maskAssets ? "••••" : `${portfolio.total_pnl >= 0 ? "+" : ""}${fmt2(portfolio.total_pnl)} (${portfolio.total_pnl_pct.toFixed(2)}%)`}
                </span>
              </div>
            </div>

            {portfolio.positions.length > 0 && (
              <div className="mt-3 min-h-0 flex-1 overflow-y-auto">
                <h3 className="section-title px-1 mb-1.5 flex-shrink-0">持仓明细</h3>
                <div className="space-y-1.5">
                  {portfolio.positions.map((p) => {
                    const mkt = (p.quantity || 0) * (p.current_price || 0);
                    return (
                    <div key={p.symbol} onClick={() => openStockDetail(p.symbol, p.name)} className="card card-lift p-3 cursor-pointer">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[12px] font-medium text-text-primary truncate flex items-center gap-1.5 min-w-0">
                          {p.name}
                          {p.t1_restricted && <span className="chip bg-warning-soft text-warning border-warning/20">T+1</span>}
                        </span>
                        <span className={`font-data text-[11px] font-semibold flex-shrink-0 inline-flex items-center gap-0.5 ${p.pnl >= 0 ? "text-up" : "text-down"}`}>
                          {p.pnl >= 0 ? <ArrowUpIcon size={10} /> : <ArrowDownIcon size={10} />}
                          {p.pnl >= 0 ? "+" : ""}{p.pnl.toFixed(2)}
                        </span>
                      </div>
                      <div className="grid grid-cols-3 gap-1.5 mt-2">
                        <div className="min-w-0"><div className="text-[11px] text-text-muted">市值</div><div className="font-data text-[11px] text-text-secondary truncate">{fmt2(mkt)}</div></div>
                        <div className="min-w-0"><div className="text-[11px] text-text-muted">成本</div><div className="font-data text-[11px] text-text-secondary truncate">{p.avg_cost.toFixed(2)}</div></div>
                        <div className="min-w-0 text-right"><div className="text-[11px] text-text-muted">盈亏%</div><Pct v={p.pnl_pct} /></div>
                      </div>
                      <div className="flex items-center justify-between mt-2 text-[11px] text-text-muted">
                        <span className="font-data">持仓 {p.quantity}股</span>
                        <span className="inline-flex items-center gap-1 flex-shrink-0">
                          <span className="chip bg-elevated border-border text-text-muted">{p.sellable_quantity ?? p.quantity}股可卖</span>
                          {p.t1_quantity > 0 && <span className="chip bg-input-bg border-border text-text-muted">{p.t1_quantity}冻结</span>}
                        </span>
                      </div>
                    </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      {addOpen && <AddWatchlistDialog onClose={() => setAddOpen(false)} />}
    </aside>
  );
}

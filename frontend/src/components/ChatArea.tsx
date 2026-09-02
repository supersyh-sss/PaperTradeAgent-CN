import { useCallback, useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import { useChatStore, hashContent } from "../stores/chatStore";
import type { AgentLog, TradingStatus } from "../api/client";
import { api } from "../api/client";
import Sidebar from "./Sidebar";
import TradePanel from "./TradePanel";

/* StockDetail 内含 echarts（~900KB），按需懒加载，避免拖慢首屏 */
const StockDetail = lazy(() => import("./StockDetail"));
import TradeConfirmPanel from "./TradeConfirmPanel";
import ActionConfirmPanel from "./ActionConfirmPanel";
import SettingsDialog from "./SettingsDialog";
import {
  getAgentProfile, LayersIcon, SearchIcon, TrendingUpIcon, ListIcon, LayoutDashboardIcon,
  SendIcon, BotIcon, TerminalIcon, MenuIcon, ExternalLinkIcon,
  DownloadIcon, FileTextIcon, SquareIcon, CopyIcon, CheckIcon, ChevronDownIcon, ChevronUpIcon, RefreshCwIcon,
  ThumbsUpIcon, ThumbsDownIcon, ThumbsUpFilledIcon, ThumbsDownFilledIcon, MaximizeIcon, MinimizeIcon,
  ShieldCheckIcon,
} from "./Icon";

/* ── Agent 完成提示音（Web Audio 合成，清脆上扬双音）── */
let _audioCtx: AudioContext | null = null;
function ensureAudioContext(): AudioContext | null {
  try {
    const AC = window.AudioContext || (window as any).webkitAudioContext;
    if (!AC) return null;
    if (!_audioCtx) _audioCtx = new AC();
    return _audioCtx;
  } catch {
    return null;
  }
}
function unlockAudio() {
  const ctx = ensureAudioContext();
  if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
}
function playCompletionSound() {
  const ctx = ensureAudioContext();
  if (!ctx) return;
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  try {
    const t0 = ctx.currentTime + 0.02;
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(0.2, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.32);
    [880, 1318.51].forEach((f, i) => {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = f;
      const start = t0 + i * 0.06;
      osc.connect(gain);
      osc.start(start);
      osc.stop(start + 0.34);
    });
  } catch {}
}

const AT_AGENTS = [
  { key: "chief_strategist", name_cn: "首席策略", color: "#60a5fa", trigger: "@助手", desc: "全局研判与任务拆解" },
  { key: "quant_researcher", name_cn: "量化分析", color: "#34d399", trigger: "@量化", desc: "技术指标与量化信号" },
  { key: "market_intelligence", name_cn: "市场情报", color: "#fbbf24", trigger: "@情报", desc: "行情与资讯情报" },
  { key: "trade_executor", name_cn: "交易执行", color: "#f87171", trigger: "@交易", desc: "交易计划与委托" },
  { key: "portfolio_monitor", name_cn: "持仓风控", color: "#a78bfa", trigger: "@风控", desc: "持仓与风控监控" },
];
const fmtTime = (t: string | number) => new Date(t).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });

const AVATAR_GRADIENTS: Record<string, [string, string]> = {
  blue: ["#3e5d8a", "#223a5e"],
  purple: ["#5a4a86", "#362a57"],
  emerald: ["#2f6e5e", "#1d463c"],
  amber: ["#8a6a35", "#57401d"],
  rose: ["#8a4a5c", "#572b39"],
};
function UserAvatar({ avatar, nickname, size = 26 }: { avatar: string; nickname: string; size?: number }) {
  if (avatar?.startsWith("data:")) {
    return <img src={avatar} alt="" style={{ width: size, height: size, objectFit: "cover" }} className="rounded-full flex-shrink-0" />;
  }
  const [from, to] = AVATAR_GRADIENTS[avatar] || AVATAR_GRADIENTS.blue;
  return (
    <div
      style={{ width: size, height: size, background: `linear-gradient(135deg, ${from}, ${to})` }}
      className="rounded-full flex items-center justify-center text-white font-semibold flex-shrink-0"
    >
      {(nickname || "投").slice(0, 1)}
    </div>
  );
}

function renderMarkdown(text: string): string {
  let html = text
    .replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>")
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n### (.*)/g, "\n<h3>$1</h3>")
    .replace(/\n## (.*)/g, "\n<h2>$1</h2>")
    .replace(/\n# (.*)/g, "\n<h1>$1</h1>")
    .replace(/\n- (.*)/g, "\n<li>$1</li>")
    .replace(/\n>/g, "\n<blockquote>")
    .replace(/\n\n/g, "<br/><br/>");
  html = html.replace(/\|(.+)\|\n\|[-| :]+\|\n((?:\|.+\|\n?)*)/g, (_m, header, rows) => {
    const ths = header.split("|").map((h: string) => h.trim()).filter(Boolean).map((h: string) => `<th>${h}</th>`).join("");
    const trs = rows.trim().split("\n").map((r: string) => {
      const tds = r.split("|").map((c: string) => c.trim()).filter(Boolean).map((c: string) => `<td>${c}</td>`).join("");
      return `<tr>${tds}</tr>`;
    }).join("");
    return `<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`;
  });
  return html.replace(/\n/g, "<br/>");
}
const stripMd = (t: string) => t.replace(/\*\*(.+?)\*\*/g, "$1").replace(/\*(.+?)\*/g, "$1")
  .replace(/^#{1,4}\s+/gm, "").replace(/`([^`]+)`/g, "$1").replace(/^[-*+]\s+/gm, "— ").replace(/\|/g, " ").trim();

/* 安全地把 Markdown 链接 [文字](url) 与裸链接渲染为可点击标签 */
function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function renderAgentContent(text: string): string {
  let html = escapeHtml(stripMd(text));
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_m: string, label: string, url: string) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer" class="news-link">${label}</a>`);
  html = html.replace(/(?<!href=")(https?:\/\/[^\s<>"')\]]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer" class="news-link">$1</a>');
  return html;
}

/* ── 复制按钮（带"已复制"瞬时反馈）── */
function CopyButton({ text, className = "" }: { text: string; className?: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={() => { navigator.clipboard?.writeText(text).catch(() => {}); setOk(true); setTimeout(() => setOk(false), 1200); }}
      className={`icon-btn w-6 h-6 rounded-md transition-colors ${className}`}
      title={ok ? "已复制" : "复制"}
      aria-label={ok ? "已复制" : "复制"}
      style={ok ? { color: "#34d399" } : undefined}
    >
      {ok ? <CheckIcon size={12} /> : <CopyIcon size={12} />}
    </button>
  );
}

/* ── 消息操作（复制 / 赞 / 踩）── */
function MessageActions({ agent, content }: { agent: string; content: string }) {
  const { feedback, sendFeedback } = useChatStore();
  const fb = feedback[hashContent(agent, content)];
  return (
    <div className="flex items-center gap-1 mt-1.5 opacity-60 group-hover:opacity-100 transition-opacity">
      <CopyButton text={content} />
      <button onClick={() => sendFeedback(agent, content, "up")} className="icon-btn w-6 h-6 rounded-md" title={fb === "up" ? "取消赞" : "赞"} style={fb === "up" ? { color: "#34d399" } : {}}>
        {fb === "up" ? <ThumbsUpFilledIcon size={14} /> : <ThumbsUpIcon size={14} />}
      </button>
      <button onClick={() => sendFeedback(agent, content, "down")} className="icon-btn w-6 h-6 rounded-md" title={fb === "down" ? "取消踩" : "踩"} style={fb === "down" ? { color: "#f87171" } : {}}>
        {fb === "down" ? <ThumbsDownFilledIcon size={14} /> : <ThumbsDownIcon size={14} />}
      </button>
    </div>
  );
}

/* ── Agent 气泡 ── */
function AgentBubble({ log, isStreaming, children }: { log: AgentLog; isStreaming?: boolean; children?: React.ReactNode }) {
  const p = getAgentProfile(log.agent); const Icon = p.icon;
  return (
    <div className="animate-fade-in group flex items-start gap-3">
      <div className="w-8 h-8 rounded-[10px] flex items-center justify-center flex-shrink-0"
        style={{ backgroundColor: p.color + "1a", color: p.color, border: `1px solid ${p.color}40` }}>
        <Icon size={15} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className="text-xs font-semibold" style={{ color: p.color }}>{p.name_cn}</span>
          <span className="text-[11px] text-text-muted font-data">{fmtTime(log.timestamp)}</span>
          {isStreaming && <span className="text-[11px] font-mono tracking-widest animate-pulse-soft" style={{ color: p.color }}>STREAMING</span>}
        </div>
        <div className="rounded-xl bg-surface-secondary/80 border border-border overflow-hidden w-fit max-w-[80%]">
          <div className="flex">
            <div className="w-[3px] flex-shrink-0" style={{ background: `linear-gradient(180deg, ${p.color}90, ${p.color}15)` }} />
            <div className="px-4 py-3 text-[13px] leading-relaxed text-text-secondary whitespace-pre-wrap break-words min-w-0">
              <span dangerouslySetInnerHTML={{ __html: renderAgentContent(log.content) }} />
              {isStreaming && <span className="inline-block w-[7px] h-[14px] ml-1 align-middle rounded-[2px] animate-pulse-soft" style={{ backgroundColor: p.color }} />}
            </div>
          </div>
        </div>
        {!isStreaming && log.content && <MessageActions agent={log.agent} content={log.content} />}
        {children}
      </div>
    </div>
  );
}

/* ── 报告卡片 ── */
function ReportActions({ text, intent }: { text: string; intent?: string }) {
  const key = ["trade", "analyze", "portfolio"].includes(intent || "") ? intent! : "reply";
  const label: Record<string, string> = { trade: "交易", analyze: "分析", portfolio: "持仓", reply: "回复" };
  const cls: Record<string, string> = {
    trade: "bg-warning-soft text-warning border-warning/20", analyze: "bg-accent-glow text-accent border-accent/20",
    portfolio: "bg-success-soft text-success border-success/20", reply: "bg-elevated text-text-muted border-border",
  };
  const openWindow = () => {
    const html = `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>PaperTradeAgent 报告</title><style>body{font-family:-apple-system,"Segoe UI","Noto Sans SC",sans-serif;max-width:800px;margin:40px auto;padding:0 24px;color:#f8fafc;background:#030712;line-height:1.8}h1{border-bottom:2px solid #60a5fa;padding-bottom:10px}h2{color:#60a5fa}th,td{border:1px solid rgba(148,163,184,.22);padding:8px 12px;text-align:left}th{background:#1e293b}td{color:#94a3b8}code{background:#1e293b;padding:2px 6px;border-radius:4px}pre{background:#020617;padding:14px;border-radius:10px;overflow-x:auto}</style></head><body>${renderMarkdown(text)}</body></html>`;
    window.open(URL.createObjectURL(new Blob([html], { type: "text/html;charset=UTF-8" })), "_blank", "width=960,height=720,scrollbars=yes");
  };
  const download = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=UTF-8" }));
    a.download = `report_${new Date().toLocaleDateString("zh-CN").replace(/\//g, "-")}.md`;
    a.click();
  };
  return (
    <div className="animate-fade-in card p-4 my-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="w-7 h-7 rounded-lg bg-accent-glow border border-accent/20 text-accent flex items-center justify-center"><FileTextIcon size={14} /></div>
        <span className={`chip ${cls[key]}`}>{label[key]}</span>
        <span className="text-[11px] text-text-muted">报告已生成</span>
        <span className="text-[11px] text-text-muted font-data">{new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={openWindow} className="btn btn-secondary px-3.5 py-2 text-[12px]"><ExternalLinkIcon size={14} />新窗口</button>
          <button onClick={download} className="btn btn-secondary px-3.5 py-2 text-[12px]"><DownloadIcon size={14} />下载报告</button>
        </div>
      </div>
    </div>
  );
}

/* ── 任务计划展开视图 ── */
function PlanView({ plan }: { plan: any }) {
  const [open, setOpen] = useState(false);
  const steps = Array.isArray(plan?.steps) ? plan.steps : [];
  if (!plan || (steps.length === 0 && !plan.reasoning)) return null;
  return (
    <div className="mt-2.5 w-fit max-w-[80%]">
      <button onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[11px] text-text-muted hover:text-text-secondary transition-colors">
        <ListIcon size={13} />
        <span>任务计划</span>
        {open ? <ChevronUpIcon size={12} /> : <ChevronDownIcon size={12} />}
      </button>
      {open && (
        <div className="mt-2 rounded-xl border border-border bg-surface-secondary/60 px-3.5 py-3 text-[12px] leading-relaxed">
          {plan.goal && (
            <div className="mb-1.5">
              <span className="text-text-muted">目标：</span>
              <span className="text-text-secondary">{plan.goal}</span>
            </div>
          )}
          {steps.length > 0 && (
            <div className="mb-1.5">
              <span className="text-text-muted">步骤：</span>
              <div className="mt-1 space-y-1">
                {steps.map((s: any, i: number) => {
                  const p = getAgentProfile(s.agent);
                  return (
                    <div key={i} className="flex items-center gap-2">
                      <span className="text-text-muted font-data">{i + 1}.</span>
                      <span style={{ color: p.color }}>{p.name_cn}</span>
                      {s.task && <span className="text-text-secondary">{s.task}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {plan.reasoning && (
            <div>
              <span className="text-text-muted">推理：</span>
              <span className="text-text-secondary">{plan.reasoning}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── CLI 终端单行动态加载动画 ── */
const CLI_SCRIPT = [
  "截断亏损，让利润奔跑",
  "Cut your losses, let your profits run",
  "别人贪婪我恐惧，别人恐惧我贪婪",
  "Be fearful when others are greedy",
  "市场永远是对的",
  "保住本金是第一原则",
  "Rule No.1: Never lose money",
  "趋势是你的朋友",
  "The trend is your friend",
  "耐心是投资者最宝贵的品质",
  "时间是优秀企业的朋友",
  "Price is what you pay, value is what you get",
  "市场短期是投票机，长期是称重机",
  "In the short run, the market is a voting machine",
  "别人恐慌时，机会就在眼前",
  "Investing is simple, but not easy",
];

function CliLoader() {
  const [lineIndex, setLineIndex] = useState(0);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    const timer = setInterval(() => {
      setLineIndex((i) => (i + 1) % CLI_SCRIPT.length);
    }, 2200);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const full = CLI_SCRIPT[lineIndex];
    const step = Math.max(25, Math.floor(1100 / Math.max(1, full.length)));
    let i = 0;
    setTyped("");
    const timer = setInterval(() => {
      i += 1;
      setTyped(full.slice(0, i));
      if (i >= full.length) clearInterval(timer);
    }, step);
    return () => clearInterval(timer);
  }, [lineIndex]);

  return (
    <div className="animate-fade-in group flex items-start gap-3">
      <div className="w-8 h-8 rounded-[10px] flex items-center justify-center flex-shrink-0 bg-accent-glow text-accent border border-accent/20">
        <TerminalIcon size={15} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className="text-xs font-semibold text-accent">系统</span>
          <span className="text-[11px] text-text-muted font-data tracking-widest">PROCESSING</span>
        </div>
        <div className="rounded-xl bg-[#070d1c] border border-accent/20 overflow-hidden w-fit max-w-[80%] min-w-[280px] shadow-[0_0_28px_rgba(96,165,250,0.10)]">
          <div className="flex items-center gap-1.5 px-3.5 py-2 border-b border-border bg-elevated/70">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
            <span className="ml-2 text-[10px] font-mono text-text-muted tracking-widest">agent-terminal</span>
          </div>
          <div className="px-4 py-3 text-[12px] leading-relaxed font-mono text-text-secondary flex items-center whitespace-nowrap">
            <span className="mr-1.5 text-accent">❯</span>
            <span>{typed}</span>
            <span className="cli-dots ml-2">
              <span className="cli-dot" />
              <span className="cli-dot" />
              <span className="cli-dot" />
              <span className="cli-dot" />
              <span className="cli-dot" />
            </span>
            <span className="cli-cursor ml-1.5" />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── 市场状态 ── */
function MarketStatusBadge() {
  const [trading, setTrading] = useState<TradingStatus | null>(null);
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const load = () => api.health().then((d) => setTrading(d.trading ?? null)).catch(() => setTrading(null));
    load();
    const t = setInterval(load, 10000);
    const c = setInterval(() => setNow(new Date()), 1000);
    return () => { clearInterval(t); clearInterval(c); };
  }, []);

  const countdown = useMemo(() => {
    if (!trading) return null;
    const at = (h: number, mi: number, base?: Date) => { const d = base ? new Date(base) : new Date(now); d.setHours(h, mi, 0, 0); return d.getTime(); };
    const cur = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
    const PRE = 9 * 3600 + 15 * 60;
    const LUNCH = 11 * 3600 + 30 * 60, NOON = 13 * 3600, CLOSE = 15 * 3600;
    let label = ""; let targetMs = 0;
    if (trading.status === "trading") {
      if (cur < LUNCH) { label = "午间休市"; targetMs = at(11, 30); }
      else { label = "收盘"; targetMs = at(15, 0); }
    } else if (trading.status === "auction") {
      label = "连续竞价开盘"; targetMs = at(9, 30);
    } else {
      const holiday = /非交易日|节假日/.test(trading.detail || "");
      if (holiday || cur >= CLOSE) { label = "下一交易日开盘"; targetMs = at(9, 30, new Date(`${trading.next_trading_day}T00:00:00`)); }
      else if (cur < PRE) { label = "集合竞价"; targetMs = at(9, 15); }
      else if (cur >= LUNCH && cur < NOON) { label = "午盘开盘"; targetMs = at(13, 0); }
      else { label = "下一交易日开盘"; targetMs = at(9, 30, new Date(`${trading.next_trading_day}T00:00:00`)); }
    }
    const secs = Math.max(0, Math.floor((targetMs - now.getTime()) / 1000));
    const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
    const text = secs <= 0 ? "即将开始" : h > 0 ? `${h}小时${m}分` : m > 0 ? `${m}分${s}秒` : `${s}秒`;
    return { label, text };
  }, [trading, now]);

  const status = trading?.status;
  const dot = status === "trading" ? "bg-success" : status === "auction" ? "bg-warning" : "bg-text-muted";
  const label = trading === null ? "..." : status === "trading" ? "交易中" : status === "auction" ? "集合竞价" : "已休市";
  return (
    <div className="flex items-center gap-2 flex-nowrap" role="status" aria-label={`市场状态：${label}`}>
      <div className="relative flex-shrink-0 group">
        <div className="chip bg-surface-secondary/60 border-border px-2 py-0.5 hover:bg-hover transition-colors whitespace-nowrap cursor-default">
          <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
            {status === "trading" && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-40" />}
            <span className={`relative inline-flex rounded-full h-1.5 w-1.5 ${dot}`} />
          </span>
          <span className="text-[11px] text-text-secondary">{label}</span>
        </div>
        <div className="absolute right-0 top-full mt-2 w-64 rounded-2xl border border-border-strong bg-elevated shadow-2xl z-50 p-3 opacity-0 invisible translate-y-1 pointer-events-none group-hover:opacity-100 group-hover:visible group-hover:translate-y-0 group-hover:pointer-events-auto transition-all duration-150">
          <div className="text-[12px] font-medium text-text-primary mb-1">交易时段</div>
          <div className="text-[11px] text-text-secondary leading-relaxed">{trading?.detail || "加载中..."}</div>
          {countdown && (
            <div className="mt-2.5 flex items-center justify-between gap-2 bg-input-bg rounded-lg px-3 py-2 border border-border">
              <span className="text-[11px] text-text-muted">距{countdown.label}</span>
              <span className="font-data text-[12px] font-semibold text-accent tabular-nums">{countdown.text}</span>
            </div>
          )}
          {trading?.next_trading_day && (
            <div className="mt-2 text-[11px] text-text-muted font-data">下一交易日：{trading.next_trading_day}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── 欢迎页 ── */
function WelcomeScreen({ onSend, onInsert }: { onSend: (t: string) => void; onInsert: (t: string) => void }) {
  const team = AT_AGENTS.map(({ key, desc }) => ({ key, desc }));
  const triggers: Record<string, string> = Object.fromEntries(AT_AGENTS.map((a) => [a.key, a.trigger]));
  const chips = [
    { text: "分析茅台", icon: SearchIcon }, { text: "查看持仓", icon: LayoutDashboardIcon },
    { text: "买入100股招商银行", icon: TrendingUpIcon }, { text: "自选股管理", icon: ListIcon },
  ];
  return (
    <div className="relative flex-1 text-center px-6 animate-fade-in overflow-hidden">
      {/* Logo 居中于第二栏约 1/4 中轴处 */}
      <div className="absolute left-1/2 top-[25%] -translate-x-1/2 -translate-y-1/2 flex flex-col items-center w-full max-w-[640px]">
        <div className="relative mb-6">
          <div className="absolute -inset-10 bg-accent/10 blur-3xl rounded-full animate-pulse-soft" />
          <div className="absolute -inset-4 bg-violet-500/8 blur-2xl rounded-full" />
          <div className="relative w-[92px] h-[92px] bg-gradient-to-br from-accent/20 via-accent/10 to-violet-500/15 rounded-[26px] flex items-center justify-center border border-accent/25 shadow-[0_0_48px_rgba(96,165,250,0.22)]">
            <LayersIcon size={44} className="text-accent" />
          </div>
        </div>
        <h1 className="text-[40px] sm:text-[52px] leading-tight font-bold tracking-tighter text-gradient-animated mb-3">PaperTradeAgent</h1>
        <p className="text-[13px] sm:text-[16px] text-gradient-animated-accent font-medium tracking-[0.12em]">A股模拟交易 · 多 Agent 协作金融终端</p>
      </div>

      <div className="absolute inset-x-0 top-1/2 pt-2 flex flex-col items-center gap-6 px-6">
        <div className="flex flex-wrap justify-center gap-3 w-full max-w-[820px]">
          {team.map(({ key, desc }, i) => {
            const p = getAgentProfile(key); const Icon = p.icon;
            return (
              <button key={key} onClick={() => onInsert(`${triggers[key]} `)} title={`${p.name_cn} · ${desc}`}
                style={{ animationDelay: `${i * 0.3}s` }}
                className="card card-lift card-breathe w-20 h-20 rounded-2xl flex flex-col items-center justify-center gap-1.5 text-center border-border hover:border-accent/30 transition-colors">
                <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ backgroundColor: p.color + "14", color: p.color, border: `1px solid ${p.color}35` }}>
                  <Icon size={13} />
                </div>
                <span className="text-[11px] font-medium text-text-primary leading-tight">{p.name_cn}</span>
              </button>
            );
          })}
        </div>

        <div className="flex gap-3 flex-wrap justify-center">
          {chips.map(({ text, icon: Icon }) => (
            <button key={text} onClick={() => onSend(text)} className="btn btn-secondary px-4 py-2.5 text-[12px] rounded-xl card-lift">
              <Icon size={14} className="text-text-muted" />{text}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-6 flex-wrap justify-center text-[11px] text-text-secondary">
          <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-success" />实时行情已连接</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-accent" />模拟交易</span>
          <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-warning" />风险监控</span>
        </div>
      </div>
    </div>
  );
}

export default function ChatArea() {
  const {
    messages, isLoading, streamingAgent, hasAgentRunThisRound, sendMessage, stopGeneration,
    actionRequired, actionPending, confirmTrade, cancelTrade, confirmGenericAction, cancelGenericAction,
    activeStockDetail, closeStockDetail, watchlist, portfolio,
    generateReport, loadFeedback, connectSessionStream, conversationId,
  } = useChatStore();
  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 1024);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profile, setProfile] = useState<{ nickname: string; avatar: string } | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [tab, setTab] = useState<"trade" | "kline">("kline");
  const [search, setSearch] = useState("");
  const [sugs, setSugs] = useState<{ code: string; name: string; exchange: string; board: string }[]>([]);
  const [sugOpen, setSugOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const debRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevLoadingRef = useRef(false);

  const firstPosition = portfolio?.positions?.[0];
  const stock = useMemo(() => activeStockDetail
    || (firstPosition ? { symbol: firstPosition.symbol, name: firstPosition.name } : null)
    || (watchlist[0] ? { symbol: watchlist[0].symbol, name: watchlist[0].name } : null),
    [activeStockDetail, firstPosition, watchlist]);

  useEffect(() => {
    const q = search.trim();
    if (!q) { setSugs([]); setSugOpen(false); return; }
    if (debRef.current) clearTimeout(debRef.current);
    debRef.current = setTimeout(async () => {
      setSearching(true);
      try { const r = await api.searchStocks(q); setSugs(r?.results || []); setSugOpen(true); }
      catch { setSugs([]); } finally { setSearching(false); }
    }, 250);
    return () => { if (debRef.current) clearTimeout(debRef.current); };
  }, [search]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, streamingAgent]);
  useEffect(() => {
    const prev = prevLoadingRef.current;
    prevLoadingRef.current = isLoading;
    if (prev && !isLoading) playCompletionSound();
  }, [isLoading]);
  useEffect(() => {
    const unlock = () => unlockAudio();
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);
  useEffect(() => { loadFeedback(); }, [loadFeedback]);
  useEffect(() => { connectSessionStream(); }, [conversationId, connectSessionStream]);
  const loadProfile = useCallback(() => {
    api.profile.get()
      .then((res) => { if (res.profile) setProfile({ nickname: res.profile.nickname, avatar: res.profile.avatar }); })
      .catch(() => {});
  }, []);
  useEffect(() => { loadProfile(); }, [loadProfile]);
  useEffect(() => { if (activeStockDetail) setTab("kline"); }, [activeStockDetail]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      setShowScrollTop(!nearBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else document.documentElement.requestFullscreen?.();
  };

  const contentMaxW = isFullscreen ? "max-w-[1800px]" : "max-w-[1280px]";

  const send = () => { if (!input.trim() || isLoading) return; unlockAudio(); sendMessage(input.trim()); setInput(""); };
  const scrollToBottom = () => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  const pickStock = (code: string, name: string) => { closeStockDetail(); useChatStore.getState().openStockDetail(code, name); setSearch(""); setSugOpen(false); };

  const panel = (
    <>
      <div className="p-3 border-b border-border flex-shrink-0 relative">
        <div className="flex items-center gap-2">
          <div className="relative flex-1 min-w-0">
            <SearchIcon size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} onFocus={() => sugs.length && setSugOpen(true)}
              onBlur={() => setTimeout(() => setSugOpen(false), 150)}
              onKeyDown={(e) => { if (e.key === "Enter" && sugs.length === 1) pickStock(sugs[0].code, sugs[0].name); }}
              placeholder="搜索股票代码或名称..." className="input py-2" style={{ paddingLeft: "2.25rem", paddingRight: "2.25rem" }} />
            {searching && <div className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />}
          </div>
        </div>
        {sugOpen && sugs.length > 0 && (
          <div className="absolute left-3 right-3 top-full mt-1 card bg-elevated shadow-2xl z-50 max-h-64 overflow-y-auto">
            {sugs.map((s) => (
              <button key={s.code} onMouseDown={(e) => { e.preventDefault(); pickStock(s.code, s.name); }}
                className={`w-full text-left px-3 py-2.5 flex items-center gap-3 transition-colors ${stock?.symbol === s.code ? "bg-accent/10" : "hover:bg-hover"}`}>
                <div className="w-7 h-7 rounded-md bg-accent-glow border border-accent/20 text-accent flex items-center justify-center flex-shrink-0">
                  <span className="text-[11px] font-bold font-data">{s.code.slice(-2)}</span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] text-text-primary truncate">{s.name}</div>
                  <div className="text-[11px] text-text-muted font-data">{s.code} · {s.board || s.exchange}</div>
                </div>
                {stock?.symbol === s.code && <span className="text-[11px] text-accent flex-shrink-0">当前</span>}
              </button>
            ))}
          </div>
        )}
      </div>
      {stock && (
        <div className="relative flex border-b border-border flex-shrink-0">
          <button onClick={() => setTab("trade")}
            className={`flex-1 py-2.5 text-[12px] font-medium transition-colors relative ${tab === "trade" ? "text-accent" : "text-text-muted hover:text-text-secondary"}`}>
            交易
            {tab === "trade" && <span className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-accent rounded-full" />}
          </button>
          <button onClick={() => setTab("kline")}
            className={`flex-1 py-2.5 text-[12px] font-medium transition-colors relative ${tab === "kline" ? "text-accent" : "text-text-muted hover:text-text-secondary"}`}>
            K线
            {tab === "kline" && <span className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-accent rounded-full" />}
          </button>
        </div>
      )}
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        {stock ? (
          tab === "trade"
            ? <TradePanel symbol={stock.symbol} name={stock.name} />
            : (
              <Suspense fallback={
                <div className="flex-1 flex items-center justify-center text-text-muted text-[12px]">K线加载中…</div>
              }>
                <StockDetail symbol={stock.symbol} name={stock.name} onClose={() => {}} />
              </Suspense>
            )
        ) : (
          <div className="flex-1 flex items-center justify-center text-text-muted text-[12px]">暂无自选股，请先添加</div>
        )}
      </div>
    </>
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* 桌面端：静态侧边栏 */}
      <Sidebar className={`hidden ${sidebarOpen ? "lg:flex" : "lg:hidden"}`} />
      {/* 移动端：抽屉式侧边栏 */}
      {sidebarOpen && (
        <>
          <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden" onClick={() => setSidebarOpen(false)} />
          <div className="fixed inset-y-0 left-0 z-50 lg:hidden slide-in-left shadow-2xl">
            <Sidebar />
          </div>
        </>
      )}
      <div className="flex-1 flex flex-col min-w-0 overflow-x-hidden">
        <header className="h-12 border-b border-border glass-strong flex items-center px-3 gap-2 flex-shrink-0">
          <div className="flex items-center justify-start flex-1 min-w-0">
            <button onClick={() => setSidebarOpen(!sidebarOpen)} className="icon-btn" title="侧边栏"><MenuIcon size={16} /></button>
          </div>
          <div className="flex-1 text-center min-w-0 px-2">
            <div className="text-[13px] font-semibold text-text-primary truncate tracking-tight whitespace-nowrap">
              {isLoading ? (
                <span className="inline-flex items-center gap-2">
                  <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse-soft" />
                  Agent 团队分析中...
                </span>
              ) : "PaperTradeAgent"}
            </div>
            <div className="text-[11px] text-text-secondary tracking-[0.22em] whitespace-nowrap">MULTI-AGENT FINANCIAL TERMINAL</div>
          </div>
          <div className="flex items-center justify-end gap-1.5 flex-1 min-w-0">
            <MarketStatusBadge />
            <button onClick={toggleFullscreen} className="icon-btn w-8 h-8 flex-shrink-0" title={isFullscreen ? "退出全屏" : "全屏"}>
              {isFullscreen ? <MinimizeIcon size={15} /> : <MaximizeIcon size={15} />}
            </button>
            {profile && (
              <button onClick={() => setSettingsOpen(true)} className="flex items-center gap-2 pl-1.5 pr-2 py-1 rounded-lg border border-transparent hover:border-border hover:bg-hover transition-colors flex-shrink-0" title="个人资料">
                <UserAvatar avatar={profile.avatar} nickname={profile.nickname} size={26} />
                <span className="text-[12px] font-medium text-text-secondary max-w-[110px] truncate hidden sm:inline">{profile.nickname}</span>
              </button>
            )}
          </div>
        </header>

        <div className="flex-1 relative min-h-0">
          <div className="absolute inset-0 overflow-y-auto" ref={scrollRef}>
            <div className={`w-full ${contentMaxW} mx-auto px-6 py-7 space-y-7 min-h-full flex flex-col`}>
            {messages.length === 0 ? <WelcomeScreen onSend={sendMessage} onInsert={setInput} /> : (
              <>
                {messages.map((msg, i) => {
                  if (msg.role === "agent" && msg.metadata?.agentLog) {
                    const log = msg.metadata.agentLog;
                    const tradePlan = actionRequired?.type === "trade_confirm" ? actionRequired.trade_plan : undefined;
                    const showConfirm = log.agent === "trade_executor" && !!tradePlan && !isLoading;
                    return (
                      <AgentBubble key={i} log={log}>
                        {showConfirm && tradePlan && (
                          <div className="mt-2 flex justify-center">
                            <div className="w-1/2 min-w-[320px]">
                              <TradeConfirmPanel tradePlan={{ ...tradePlan, prev_close: tradePlan.prev_close ?? tradePlan.price }}
                                onConfirm={(p, q, f) => confirmTrade(p, q, f)} onCancel={cancelTrade} />
                            </div>
                          </div>
                        )}
                      </AgentBubble>
                    );
                  }
                  if (msg.role === "user") {
                    return (
                      <div key={i} className="animate-fade-in group flex items-start gap-3 justify-end">
                        <div className="max-w-[80%] flex flex-col items-end">
                          <div className="flex items-baseline gap-2 mb-1 pr-1">
                            <span className="text-xs font-semibold text-text-secondary">{profile?.nickname || "用户"}</span>
                            <span className="text-[11px] text-text-muted font-data">{fmtTime(msg.timestamp)}</span>
                          </div>
                          <div className="rounded-2xl rounded-br-md px-4 py-3 text-[13px] leading-relaxed text-text-primary border border-blue-400/25 w-fit max-w-full whitespace-pre-wrap break-words"
                            style={{ background: "linear-gradient(135deg, rgba(59,130,246,0.16), rgba(139,92,246,0.10))" }}>
                            {msg.content}
                          </div>
                          <div className="flex items-center gap-1 mt-1.5 opacity-60 group-hover:opacity-100 transition-opacity">
                            <CopyButton text={msg.content} />
                            <button onClick={() => sendMessage(msg.content)} disabled={isLoading} className="icon-btn w-6 h-6 rounded-md disabled:opacity-40" title="重新生成" aria-label="重新生成"><RefreshCwIcon size={12} /></button>
                          </div>
                        </div>
                        <UserAvatar avatar={profile?.avatar || ""} nickname={profile?.nickname || "用户"} size={32} />
                      </div>
                    );
                  }
                  if (msg.metadata?.monitor) {
                    const m = msg.metadata.monitorData;
                    const sev = m?.severity || "info";
                    const color = sev === "danger" ? "#f87171" : sev === "warning" ? "#fbbf24" : "#60a5fa";
                    return (
                      <div key={i} className="animate-fade-in flex items-start gap-3">
                        <div className="w-8 h-8 rounded-[10px] flex items-center justify-center flex-shrink-0" style={{ backgroundColor: color + "1a", color, border: `1px solid ${color}40` }}>
                          <ShieldCheckIcon size={15} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-baseline gap-2 mb-1.5">
                            <span className="text-xs font-semibold" style={{ color }}>风控监控</span>
                            <span className="text-[11px] text-text-muted font-data">{fmtTime(msg.timestamp)}</span>
                          </div>
                          <div className="rounded-xl border overflow-hidden w-fit max-w-[80%]" style={{ backgroundColor: color + "0a", borderColor: color + "30" }}>
                            <div className="flex">
                              <div className="w-[3px] flex-shrink-0" style={{ background: `linear-gradient(180deg, ${color}90, ${color}15)` }} />
                              <div className="px-4 py-3 text-[13px] leading-relaxed text-text-secondary whitespace-pre-wrap break-words min-w-0">
                                {m?.title && <div className="font-semibold mb-1" style={{ color }}>{m.title}</div>}
                                <div>{msg.content}</div>
                                {Array.isArray(m?.positions) && m.positions.length > 0 && (
                                  <div className="mt-2.5 rounded-lg border border-border bg-surface-secondary/60 overflow-hidden max-w-[380px]">
                                    <div className="px-2.5 py-1.5 text-[11px] font-semibold text-text-muted bg-elevated/60 flex items-center justify-between">
                                      <span>当前持仓快照</span>
                                      {typeof m.total_pnl_pct === "number" && (
                                        <span className={`font-data ${m.total_pnl_pct >= 0 ? "text-up" : "text-down"}`}>组合 {m.total_pnl_pct >= 0 ? "+" : ""}{m.total_pnl_pct.toFixed(2)}%</span>
                                      )}
                                    </div>
                                    <table className="w-full text-[11px] font-data">
                                      <thead>
                                        <tr className="text-text-disabled">
                                          <th className="text-left px-2.5 py-1 font-normal">股票</th>
                                          <th className="text-right px-2.5 py-1 font-normal">市值</th>
                                          <th className="text-right px-2.5 py-1 font-normal">盈亏</th>
                                        </tr>
                                      </thead>
                                      <tbody>
                                        {m.positions.map((p: any) => (
                                          <tr key={p.symbol} className="border-t border-border/60 hover:bg-hover/50">
                                            <td className="px-2.5 py-1.5 text-text-primary">
                                              <span className="block truncate max-w-[120px]">{p.name}</span>
                                              <span className="text-[10px] text-text-muted">{p.symbol}</span>
                                            </td>
                                            <td className="text-right px-2.5 py-1.5 text-text-secondary">{typeof p.market_value === "number" ? p.market_value.toFixed(2) : "-"}</td>
                                            <td className={`text-right px-2.5 py-1.5 font-semibold ${p.pnl >= 0 ? "text-up" : "text-down"}`}>
                                              {p.pnl >= 0 ? "+" : ""}{typeof p.pnl === "number" ? p.pnl.toFixed(2) : "-"}
                                              <span className="block text-[10px] font-normal">{p.pnl >= 0 ? "+" : ""}{typeof p.pnl_pct === "number" ? p.pnl_pct.toFixed(2) : "-"}%</span>
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                                {m?.suggested_prompt && (
                                  <button onClick={() => sendMessage(m.suggested_prompt!)} disabled={isLoading}
                                    className="btn btn-secondary mt-2.5 px-3.5 py-1.5 text-[12px] disabled:opacity-50">
                                    按此建议执行
                                  </button>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  }
                  const isReport = msg.metadata?.intent && !["chat", "watchlist"].includes(msg.metadata.intent) && msg.metadata?.needs_report !== false;
                  if (isReport && msg.content) return (
                    <div key={i} className="animate-fade-in flex items-start gap-3">
                      <div className="w-8 h-8 flex-shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="w-fit max-w-[80%]">
                          <ReportActions text={msg.content} intent={msg.metadata?.intent} />
                        </div>
                      </div>
                    </div>
                  );
                  if (msg.content) {
                    const suggestReport = msg.metadata?.suggest_report && msg.metadata?.needs_report === false;
                    return (
                      <div key={i} className="animate-fade-in group flex items-start gap-3">
                        <div className="w-8 h-8 rounded-[10px] flex items-center justify-center flex-shrink-0 bg-accent-glow text-accent border border-accent/20"><BotIcon size={15} /></div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-baseline gap-2 mb-1.5"><span className="text-xs font-semibold text-accent">助手</span></div>
                          <div className="rounded-xl bg-surface-secondary/80 border border-border overflow-hidden w-fit max-w-[80%]">
                            <div className="flex">
                              <div className="w-[3px] bg-accent/50 flex-shrink-0" />
                              <div className="px-4 py-3 text-[13px] leading-relaxed text-text-secondary whitespace-pre-wrap break-words min-w-0"><span dangerouslySetInnerHTML={{ __html: renderAgentContent(msg.content) }} /></div>
                            </div>
                          </div>
                          <MessageActions agent="assistant" content={msg.content} />
                          {msg.metadata?.plan && <PlanView plan={msg.metadata.plan} />}
                          {suggestReport && (
                            <div className="mt-2.5 flex items-center gap-2.5">
                              <span className="text-[11px] text-text-muted">分析已完成，需要生成完整报告吗？</span>
                              <button onClick={() => generateReport(msg.metadata?.intent || "analyze")} disabled={actionPending}
                                className="btn btn-primary px-3.5 py-1.5 text-[12px] disabled:opacity-50">
                                {actionPending ? <span className="btn-spinner" /> : <FileTextIcon size={13} />} 生成报告
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  }
                  return null;
                })}
                {actionRequired && actionRequired.type !== "trade_confirm" && !isLoading && (
                  <div className="animate-fade-in flex justify-center">
                    <div className="w-1/2 min-w-[320px]">
                      <ActionConfirmPanel action={actionRequired} onConfirm={confirmGenericAction} onCancel={cancelGenericAction} />
                    </div>
                  </div>
                )}
                {isLoading && !streamingAgent && !hasAgentRunThisRound && <CliLoader />}
                {isLoading && streamingAgent && <AgentBubble log={streamingAgent} isStreaming />}
                <div ref={endRef} />
              </>
            )}
            </div>
          </div>
          {showScrollTop && (
            <button onClick={scrollToBottom}
              className="absolute bottom-5 right-5 icon-btn w-9 h-9 rounded-full bg-elevated border border-border shadow-xl text-text-muted hover:text-text-primary z-10"
              title="回到底部">
              <ChevronDownIcon size={16} />
            </button>
          )}
        </div>

        <div className="border-t border-border glass-strong px-5 sm:px-8 pt-3 pb-3 flex-shrink-0">
          <div className={`w-full ${contentMaxW} mx-auto`}>
            <div className="flex items-center gap-2 mb-2.5 flex-wrap">
              <span className="text-[11px] font-mono text-text-muted">@</span>
              {AT_AGENTS.map((a) => (
                <button key={a.key} disabled={isLoading} title={`${a.name_cn} · ${a.desc}`}
                  onClick={() => { setInput((v) => (v.trim() ? `${v.trim()} ${a.trigger} ` : `${a.trigger} `)); setTimeout(() => inputRef.current?.focus(), 0); }}
                  className="chip transition-transform hover:scale-105 active:scale-95 disabled:opacity-40"
                  style={{ color: a.color, borderColor: a.color + "30", backgroundColor: a.color + "0a" }}>
                  {a.trigger}
                </button>
              ))}
            </div>
            <div className="relative">
              <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                onInput={(e) => { const t = e.target as HTMLTextAreaElement; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }}
                placeholder="输入股票名称或代码，进行分析、交易或查询..." rows={3}
                className="input resize-none rounded-2xl" style={{ minHeight: 96, paddingRight: "3.5rem" }} />
              {isLoading ? (
                <button onClick={stopGeneration} title="停止生成"
                  className="btn btn-primary absolute right-2.5 top-1/2 -translate-y-1/2 w-10 h-10 rounded-xl"
                  style={{ background: "linear-gradient(135deg,#ef4444,#dc2626)" }} aria-label="停止">
                  <SquareIcon size={13} />
                </button>
              ) : (
                <button onClick={send} disabled={!input.trim()}
                  className="btn btn-primary absolute right-2.5 top-1/2 -translate-y-1/2 w-10 h-10 rounded-xl disabled:opacity-35 disabled:cursor-not-allowed disabled:shadow-none"
                  aria-label="发送">
                  <SendIcon size={16} />
                </button>
              )}
            </div>
            <p className="text-[11px] text-text-muted text-center mt-2">PaperTradeAgent · A股模拟交易 · 不构成真实投资建议 · 股市有风险，投资需谨慎</p>
          </div>
        </div>
      </div>

      {drawerOpen && <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden" onClick={() => setDrawerOpen(false)} />}
      <aside className={`fixed lg:static inset-y-0 right-0 z-50 lg:z-auto w-[min(400px,92vw)] lg:w-[360px] xl:w-[400px] flex-shrink-0 border-l border-border-strong bg-bg-elevated/90 glass flex flex-col overflow-x-hidden transition-transform duration-300 ${drawerOpen ? "translate-x-0" : "translate-x-full lg:translate-x-0"}`}>
        {panel}
      </aside>
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onProfileChanged={loadProfile}
      />
    </div>
  );
}

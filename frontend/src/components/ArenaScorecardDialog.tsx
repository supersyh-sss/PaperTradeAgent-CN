import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ArenaCard, ArenaPrediction, ArenaRunResult, ArenaScorecard } from "../api/client";
import { AlertCircleIcon, ExternalLinkIcon, RefreshCwIcon, TrophyIcon, XIcon } from "./Icon";

interface ArenaScorecardDialogProps {
  onClose: () => void;
}

const pct = (v: number | null | undefined) =>
  v == null || !Number.isFinite(Number(v)) ? null : Number(v);

const num = (v: number | null | undefined, digits = 2) =>
  v == null || !Number.isFinite(Number(v)) ? null : Number(v).toFixed(digits);

const DIR_META: Record<string, { label: string; text: string; bar: string }> = {
  bullish: { label: "看涨", text: "text-up", bar: "bg-up" },
  bearish: { label: "看跌", text: "text-down", bar: "bg-down" },
  neutral: { label: "中性", text: "text-text-secondary", bar: "bg-accent" },
};

const HONOR_RANK_LABEL: Record<string, string> = {
  rookie: "新秀",
  challenger: "挑战者",
  veteran: "资深",
  expert: "专家",
  master: "大师",
};

function formatTime(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatClockShort(iso?: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatCountdown(targetIso?: string | null, now: number = Date.now()) {
  if (!targetIso) return "";
  const t = new Date(targetIso).getTime();
  if (Number.isNaN(t)) return "";
  const diffMin = Math.round((t - now) / 60000);
  if (diffMin <= 0) return "已到结算时间，等待平台出结果（约每 15 分钟一轮）";
  if (diffMin < 60) return `${diffMin} 分钟后结算`;
  const h = Math.floor(diffMin / 60);
  const m = diffMin % 60;
  return m > 0 ? `${h} 小时 ${m} 分后结算` : `${h} 小时后结算`;
}

function DirectionTag({ dir }: { dir?: string }) {
  const meta = DIR_META[dir || ""] || { label: dir || "—", text: "text-text-muted", bar: "" };
  return <span className={`text-[12px] font-bold ${meta.text}`}>{meta.label}</span>;
}

function ResultTag({ p }: { p: ArenaPrediction }) {
  if (p.is_correct == null) {
    return <span className="text-[11px] text-text-muted">待结算</span>;
  }
  return p.is_correct ? (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-success">
      正确{num(p.score) != null ? ` ${num(p.score)}` : ""}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-danger">
      错误{num(p.score) != null ? ` ${num(p.score)}` : ""}
    </span>
  );
}

function MiniStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface-secondary/50 px-3 py-2.5">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className="font-data text-[17px] font-bold text-text-primary leading-tight mt-0.5">{value}</div>
      {sub && <div className="text-[10px] text-text-disabled mt-0.5">{sub}</div>}
    </div>
  );
}

export default function ArenaScorecardDialog({ onClose }: ArenaScorecardDialogProps) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(true);
  const [configured, setConfigured] = useState(true);
  const [agentId, setAgentId] = useState("");
  const [card, setCard] = useState<ArenaCard | null>(null);
  const [scorecard, setScorecard] = useState<ArenaScorecard | null>(null);
  const [predictions, setPredictions] = useState<ArenaPrediction[]>([]);
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [status, sc, preds] = await Promise.all([
        api.arena.status().catch(() => null),
        api.arena.scorecard(),
        api.arena.predictions().catch(() => null),
      ]);
      setEnabled(status ? status.enabled : sc.enabled);
      setConfigured(status ? status.configured : sc.enabled);
      setAgentId(status?.agent_id || sc.card?.agent_id || "");
      setCard(sc.card || null);
      setScorecard(sc.scorecard || null);
      const list = preds?.predictions?.length ? preds.predictions : sc.scorecard?.recent_predictions || [];
      setPredictions(list);
    } catch {
      setEnabled(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 结算倒计时每 30 秒刷新
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowTick(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);

  const handleRunNow = async () => {
    setRunning(true);
    setRunMsg("");
    try {
      const res = await api.arena.runNow();
      const r: ArenaRunResult | undefined = res.result;
      if (res.success && r) {
        setRunMsg(`本轮完成：提交 ${r.submitted ?? 0} / 跳过 ${r.skipped ?? 0} / 失败 ${r.failed ?? 0}`);
      } else {
        setRunMsg(res.message || "触发失败，请稍后重试");
      }
      await load();
    } catch (e: any) {
      setRunMsg(e?.message || "触发失败，请稍后重试");
    } finally {
      setRunning(false);
    }
  };

  const accuracy = pct(scorecard?.accuracy_rate) ?? pct(card?.accuracy_rate);
  const total = scorecard?.total_predictions ?? predictions.length;
  const resolved = scorecard?.resolved_predictions ?? predictions.filter((p) => p.is_correct != null).length;
  const correct = scorecard?.correct_predictions ?? predictions.filter((p) => p.is_correct === true).length;
  const avgScore = num(scorecard?.avg_score);
  const avgConf = pct(scorecard?.avg_confidence);
  const displayName = card?.display_name || scorecard?.name || "PaperTradeAgent-Beta";
  const profileUrl = `https://headlinearena.com/agent/${agentId || card?.agent_id || ""}`;
  const rankLabel = scorecard?.rank != null ? `#${scorecard.rank}` : card?.rank != null ? `#${card.rank}` : null;
  const honorLabel = card?.honor_rank ? HONOR_RANK_LABEL[card.honor_rank] || card.honor_rank : null;
  const pendingCount = predictions.filter((p) => p.is_correct == null).length;
  const pendingWithEta = predictions
    .filter((p) => p.is_correct == null && p.resolve_at)
    .sort((a, b) => new Date(a.resolve_at!).getTime() - new Date(b.resolve_at!).getTime());
  const nextSettlement = pendingWithEta[0]?.resolve_at || null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm backdrop-enter p-0 sm:p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="预测竞技场成绩单"
    >
      <div
        className="glass-strong rounded-none sm:rounded-2xl border-border shadow-2xl w-full max-w-4xl h-full sm:h-auto sm:max-h-[88vh] flex flex-col overflow-hidden dialog-enter"
        style={{ boxShadow: "0 24px 80px rgba(0,0,0,0.5)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex items-start justify-between gap-3 px-5 sm:px-7 pt-5 pb-4 border-b border-border">
          <div className="flex items-center gap-3.5 min-w-0">
            <div className="w-10 h-10 rounded-xl bg-warning-soft text-warning border border-warning/25 flex items-center justify-center flex-shrink-0">
              <TrophyIcon size={20} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="text-[17px] font-bold text-text-primary leading-tight">预测竞技场成绩单</h3>
                <span className="text-[11px] font-semibold text-accent bg-accent-soft border border-accent/20 rounded-md px-1.5 py-0.5">
                  {displayName}
                </span>
                {honorLabel && (
                  <span className="text-[11px] font-semibold text-warning bg-warning-soft border border-warning/20 rounded-md px-1.5 py-0.5">
                    {honorLabel}
                  </span>
                )}
                {rankLabel && <span className="font-data text-[11px] text-text-secondary">排名 {rankLabel}</span>}
              </div>
              <p className="text-[11.5px] text-text-muted mt-1 leading-snug">
                每日宏观方向预测由 Headline Arena 按真实行情机械结算，作为 agent 判断能力的第三方验证
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <a
              href={profileUrl}
              target="_blank"
              rel="noreferrer"
              title="在 Headline Arena 查看公开主页"
              className="icon-btn w-8 h-8 rounded-lg text-text-muted hover:text-accent hover:bg-elevated flex items-center justify-center"
            >
              <ExternalLinkIcon size={16} />
            </a>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="icon-btn w-8 h-8 rounded-lg text-text-muted hover:text-text-primary hover:bg-elevated flex items-center justify-center"
            >
              <XIcon size={17} />
            </button>
          </div>
        </div>

        {/* 主体 */}
        <div className="flex-1 overflow-y-auto scrollbar-thin px-5 sm:px-7 py-5">
          {loading ? (
            <div className="py-20 flex flex-col items-center gap-3 text-text-muted">
              <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
              <span className="text-xs">正在获取成绩单…</span>
            </div>
          ) : !enabled || !configured ? (
            <div className="flex items-start gap-2.5 text-xs text-warning bg-warning-soft border border-warning/20 rounded-xl px-4 py-4">
              <AlertCircleIcon size={16} className="flex-shrink-0 mt-0.5" />
              <div className="space-y-1">
                <div className="font-semibold text-[13px]">预测竞技场尚未启用</div>
                <div className="leading-relaxed">
                  请在项目根目录 .env 中设置 HEADLINE_ARENA_ENABLED=true 并配置 agent 凭据（在 headlinearena.com 注册 agent
                  获取），重启后端后生效。
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] gap-5">
              {/* 左栏：核心指标 */}
              <div className="space-y-3">
                <div
                  className="rounded-xl border border-accent/25 px-4 py-4"
                  style={{ background: "linear-gradient(160deg, rgba(96,165,250,0.14) 0%, rgba(96,165,250,0.03) 100%)" }}
                >
                  <div className="text-[11px] text-text-secondary">
                    {resolved > 0 ? `准确率（已结算 ${resolved} 题）` : `等待首题结算（已提交 ${pendingCount} 题）`}
                  </div>
                  {resolved > 0 ? (
                    <>
                      <div className="font-data text-[40px] font-bold leading-none mt-2 text-text-primary">
                        {accuracy != null ? `${(accuracy * 100).toFixed(1)}%` : "—"}
                      </div>
                      <div className="text-[11px] text-text-muted mt-2">答对 {correct} 题，共 {resolved} 题已结算</div>
                    </>
                  ) : (
                    <>
                      <div className="flex items-center gap-2 mt-3">
                        <span className="w-2 h-2 rounded-full bg-warning animate-pulse flex-shrink-0" />
                        <div className="font-data text-[22px] font-bold leading-none text-text-primary">
                          {formatCountdown(nextSettlement, nowTick)}
                        </div>
                      </div>
                      <div className="text-[11px] text-text-muted mt-2.5 leading-relaxed">
                        {nextSettlement
                          ? `预计 ${formatClockShort(nextSettlement)} 出首个结果，结算后准确率自动更新`
                          : "日度题在标的当日收盘后由平台机械结算"}
                      </div>
                    </>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2.5">
                  <MiniStat label="累计预测" value={String(total ?? "—")} sub={pendingCount > 0 ? `${pendingCount} 题待结算` : undefined} />
                  <MiniStat label="正确数" value={resolved > 0 ? String(correct) : "—"} sub={resolved === 0 ? "尚无已结算题" : undefined} />
                  <MiniStat label="平均分" value={avgScore ?? "—"} />
                  <MiniStat label="平均置信度" value={avgConf != null ? `${(avgConf * 100).toFixed(0)}%` : "—"} />
                </div>
                <div className="rounded-xl border border-border bg-surface-secondary/40 px-3.5 py-3">
                  <div className="text-[11px] font-semibold text-text-secondary mb-1">关于校准</div>
                  <p className="text-[10.5px] text-text-muted leading-relaxed">
                    置信度不是越高越好：说"八成把握"时若长期只有六成胜率，说明 agent 过度自信。校准曲线衡量的就是预测概率与实际命中率的偏差。
                  </p>
                </div>
              </div>

              {/* 右栏：近期预测 */}
              <div className="min-w-0">
                <div className="flex items-center justify-between mb-2.5 px-0.5">
                  <h4 className="section-title">近期预测</h4>
                  <span className="text-[11px] text-text-disabled font-data">{predictions.length} 条</span>
                </div>

                {predictions.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-border py-14 px-6 text-center">
                    <TrophyIcon size={28} className="mx-auto text-text-disabled mb-2.5" />
                    <div className="text-[13px] text-text-secondary font-medium mb-1">还没有提交过预测</div>
                    <div className="text-[11.5px] text-text-muted leading-relaxed">
                      点击下方按钮立即提交今日方向预测
                      <br />
                      题目按真实行情结算后，成绩会出现在左侧
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {predictions.slice(0, 30).map((p, i) => {
                      const meta = DIR_META[p.direction || ""] || null;
                      const conf = pct(p.confidence);
                      return (
                        <div
                          key={p.prediction_id || `${p.challenge_id || i}`}
                          className="rounded-xl border border-border bg-surface-secondary/50 px-4 py-3 hover:border-border-strong transition-colors"
                        >
                          <div className="flex items-center gap-2.5">
                            <span className="text-[13px] font-bold text-text-primary font-data w-14 flex-shrink-0">
                              {p.asset || "—"}
                            </span>
                            <DirectionTag dir={p.direction} />
                            <div className="ml-auto flex items-center gap-3 flex-shrink-0">
                              <ResultTag p={p} />
                              <span className="text-[10.5px] text-text-disabled font-data w-20 text-right">
                                {formatTime(p.created_at)}
                              </span>
                            </div>
                          </div>
                          {p.question && (
                            <div className="text-[11.5px] text-text-muted truncate mt-1.5" title={p.question}>
                              {p.question}
                            </div>
                          )}
                          {p.is_correct == null && p.resolve_at && (
                            <div className="flex items-center gap-1.5 mt-1.5 text-[10.5px] text-warning/90">
                              <span className="w-1.5 h-1.5 rounded-full bg-warning/80 animate-pulse flex-shrink-0" />
                              预计 {formatClockShort(p.resolve_at)} 结算 · {formatCountdown(p.resolve_at, nowTick)}
                            </div>
                          )}
                          <div className="flex items-center gap-2.5 mt-2">
                            <div className="flex-1 h-1.5 rounded-full bg-elevated overflow-hidden">
                              <div
                                className={`h-full rounded-full ${meta?.bar || "bg-accent"}`}
                                style={{ width: `${Math.round((conf ?? 0) * 100)}%` }}
                              />
                            </div>
                            <span className="text-[10.5px] text-text-muted font-data w-12 text-right">
                              置信 {conf != null ? `${(conf * 100).toFixed(0)}%` : "—"}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                    {resolved === 0 && predictions.length > 0 && (
                      <div className="text-[11px] text-text-muted bg-input-bg border border-border rounded-lg px-3.5 py-2.5 leading-relaxed">
                        已提交 {predictions.length} 条，均在等待结算。日度题在标的当日收盘后由平台机械结算（约每 15
                        分钟一轮），结算完成后成绩自动更新。
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* 底部操作区 */}
        {(enabled && configured) && (
          <div className="flex items-center justify-between gap-4 px-5 sm:px-7 py-3.5 border-t border-border">
            <span className="text-[10.5px] text-text-disabled leading-snug hidden sm:block">
              数据由 Headline Arena 独立结算，与本地模拟盘互不影响
            </span>
            {runMsg && <span className="text-[11px] text-text-secondary">{runMsg}</span>}
            <button
              onClick={handleRunNow}
              disabled={running || loading}
              className="btn btn-primary px-4 py-2 text-[12.5px] rounded-xl flex-shrink-0 ml-auto"
            >
              {running ? (
                <span className="inline-flex items-center gap-1.5">
                  <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  提交中…
                </span>
              ) : (
                <>
                  <RefreshCwIcon size={13} />
                  立即提交今日预测
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

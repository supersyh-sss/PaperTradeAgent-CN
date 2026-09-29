import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ArenaPrediction, ArenaRunResult, ArenaScorecard } from "../api/client";
import { AlertCircleIcon, ExternalLinkIcon, RefreshCwIcon, TrophyIcon, XIcon } from "./Icon";

interface ArenaScorecardDialogProps {
  onClose: () => void;
}

const pct = (v: number | null | undefined) =>
  v == null || !Number.isFinite(Number(v)) ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

const num = (v: number | null | undefined, digits = 2) =>
  v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(digits);

const DIR_META: Record<string, { label: string; cls: string }> = {
  bullish: { label: "看涨", cls: "text-up" },
  bearish: { label: "看跌", cls: "text-down" },
  neutral: { label: "中性", cls: "text-text-muted" },
};

function DirectionChip({ dir }: { dir?: string }) {
  const meta = DIR_META[dir || ""] || { label: dir || "—", cls: "text-text-muted" };
  return <span className={`text-[11px] font-semibold ${meta.cls}`}>{meta.label}</span>;
}

function ResultChip({ p }: { p: ArenaPrediction }) {
  if (p.is_correct == null) {
    return <span className="chip bg-input-bg border-border text-text-muted">进行中</span>;
  }
  return p.is_correct ? (
    <span className="chip bg-success-soft border-success/20 text-success">正确 {p.score != null ? num(p.score) : ""}</span>
  ) : (
    <span className="chip bg-danger-soft border-danger/20 text-danger">错误 {p.score != null ? num(p.score) : ""}</span>
  );
}

export default function ArenaScorecardDialog({ onClose }: ArenaScorecardDialogProps) {
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(true);
  const [configured, setConfigured] = useState(true);
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
      setScorecard(sc.scorecard || null);
      // 优先取 /predictions 的完整历史，降级用成绩单里的近期列表
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

  const metrics: { label: string; value: string }[] = [
    { label: "累计预测", value: String(scorecard?.total_predictions ?? "—") },
    { label: "已结算", value: String(scorecard?.resolved_predictions ?? "—") },
    { label: "正确数", value: String(scorecard?.correct_predictions ?? "—") },
    { label: "准确率", value: pct(scorecard?.accuracy_rate) },
    { label: "平均分", value: num(scorecard?.avg_score) },
    { label: "平均置信度", value: pct(scorecard?.avg_confidence) },
    { label: "排名", value: scorecard?.rank != null ? `#${scorecard.rank}` : "—" },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm backdrop-enter"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="预测竞技场成绩单"
    >
      <div
        className="glass-strong rounded-2xl border border-border shadow-2xl p-5 w-[560px] max-w-[94vw] max-h-[86vh] overflow-y-auto scrollbar-thin dialog-enter"
        style={{ boxShadow: "0 24px 60px rgba(0,0,0,0.45)" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题行 */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-7 h-7 rounded-lg bg-warning-soft text-warning border border-warning/20 flex items-center justify-center flex-shrink-0">
              <TrophyIcon size={16} />
            </div>
            <h3 className="text-base font-bold text-text-primary">预测竞技场成绩单</h3>
            {scorecard?.name && (
              <span className="chip bg-accent-soft border-accent/20 text-accent truncate max-w-[160px]">{scorecard.name}</span>
            )}
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <a
              href="https://headlinearena.com"
              target="_blank"
              rel="noreferrer"
              title="在 Headline Arena 查看个人页"
              className="icon-btn w-7 h-7 rounded-lg text-text-muted hover:text-accent hover:bg-elevated"
            >
              <ExternalLinkIcon size={15} />
            </a>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="icon-btn w-7 h-7 rounded-lg text-text-muted hover:text-text-primary hover:bg-elevated"
            >
              <XIcon size={16} />
            </button>
          </div>
        </div>
        <p className="text-xs text-text-muted mb-4 ml-9">
          每日宏观方向预测由 Headline Arena 按真实行情机械结算（第三方验证）
        </p>

        {loading ? (
          <div className="py-10 flex flex-col items-center gap-2 text-text-muted">
            <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            <span className="text-xs">正在获取成绩单…</span>
          </div>
        ) : !enabled || !configured ? (
          /* 未启用 / 未配置引导 */
          <div className="flex items-start gap-2 text-xs text-warning bg-warning-soft border border-warning/20 rounded-xl px-3 py-3 mb-2">
            <AlertCircleIcon size={14} className="flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold mb-0.5">预测竞技场尚未启用</div>
              <div>请在项目根目录 .env 中设置 HEADLINE_ARENA_ENABLED=true 并配置 agent 凭据（在 headlinearena.com 注册 agent 获取），重启后端后生效。</div>
            </div>
          </div>
        ) : (
          <>
            {/* 指标卡片行 */}
            <div className="grid grid-cols-4 gap-2 mb-4">
              {metrics.map((m) => (
                <div key={m.label} className="card p-2.5 min-w-0">
                  <div className="text-[11px] text-text-muted truncate">{m.label}</div>
                  <div className="font-data text-[15px] font-bold text-text-primary leading-tight mt-0.5">{m.value}</div>
                </div>
              ))}
            </div>

            {/* 近期预测列表 */}
            <h4 className="section-title px-1 mb-1.5">近期预测</h4>
            {predictions.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border py-6 text-center text-[12px] text-text-muted mb-4">
                暂无预测记录，可点击下方按钮立即提交今日预测
              </div>
            ) : (
              <div className="space-y-1.5 mb-4 max-h-[260px] overflow-y-auto pr-0.5 scrollbar-thin">
                {predictions.slice(0, 20).map((p, i) => (
                  <div key={p.prediction_id || `${p.challenge_id || i}`} className="card p-2.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[12px] font-semibold text-text-primary font-data flex-shrink-0">{p.asset || "—"}</span>
                      <DirectionChip dir={p.direction} />
                      <span className="font-data text-[11px] text-text-muted flex-shrink-0">置信度 {pct(p.confidence)}</span>
                      <div className="ml-auto flex-shrink-0">
                        <ResultChip p={p} />
                      </div>
                    </div>
                    {p.question && (
                      <div className="text-[11px] text-text-secondary truncate mt-1" title={p.question}>
                        {p.question}
                      </div>
                    )}
                    {p.created_at && (
                      <div className="text-[10px] text-text-disabled mt-0.5 font-data">
                        {new Date(p.created_at).toLocaleString("zh-CN", { hour12: false })}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 操作区 */}
            {runMsg && <div className="text-[11px] text-text-secondary bg-input-bg border border-border rounded-lg px-3 py-2 mb-2">{runMsg}</div>}
            <div className="flex items-center justify-between gap-3 pt-3 border-t border-border">
              <span className="text-[10px] text-text-disabled leading-snug">
                数据由 Headline Arena 按真实行情独立结算，与本地模拟盘相互独立
              </span>
              <button onClick={handleRunNow} disabled={running} className="btn btn-primary px-3.5 py-2 text-[12px] rounded-xl flex-shrink-0">
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
          </>
        )}
      </div>
    </div>
  );
}

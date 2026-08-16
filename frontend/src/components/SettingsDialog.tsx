import { useState, useEffect, useRef } from "react";
import { api, type ObservabilityOverview, type ScheduledTask, type RiskQuestion } from "../api/client";
import { XIcon, CheckCircleIcon, AlertCircleIcon, UploadIcon } from "./Icon";

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
  onProfileChanged?: () => void;
}

const TABS = [
  { key: "profile", label: "个人资料" },
  { key: "model", label: "模型配置" },
  { key: "ratelimit", label: "行情限流" },
  { key: "data", label: "数据与交易" },
  { key: "timeout", label: "超时与记忆" },
  { key: "scheduler", label: "定时任务" },
  { key: "metrics", label: "运行统计" },
] as const;

const AVATAR_OPTIONS = [
  { id: "blue", from: "#3e5d8a", to: "#223a5e" },
  { id: "purple", from: "#5a4a86", to: "#362a57" },
  { id: "emerald", from: "#2f6e5e", to: "#1d463c" },
  { id: "amber", from: "#8a6a35", to: "#57401d" },
  { id: "rose", from: "#8a4a5c", to: "#572b39" },
];

const RISK_LABELS: Record<string, string> = {
  conservative: "保守型",
  balanced: "均衡型",
  aggressive: "进取型",
};

function compressImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const size = 256;
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d")!;
      const side = Math.min(img.width, img.height);
      const sx = (img.width - side) / 2;
      const sy = (img.height - side) / 2;
      ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图片加载失败"));
    };
    img.src = url;
  });
}

type TabKey = (typeof TABS)[number]["key"];

const PASSWORD_PLACEHOLDER = "••••••••";

interface FieldDef {
  key: string;
  label: string;
  type: "text" | "number" | "password";
  description?: string;
  tab: TabKey;
}

const FIELDS: FieldDef[] = [
  // 模型配置
  { key: "DEEPSEEK_API_KEY", label: "DEEPSEEK_API_KEY", type: "password", tab: "model" },
  { key: "DEEPSEEK_BASE_URL", label: "DEEPSEEK_BASE_URL", type: "text", tab: "model" },
  { key: "DEEPSEEK_FLASH_MODEL", label: "DEEPSEEK_FLASH_MODEL", type: "text", description: "用于快速响应场景（如意图识别、简单问答）的轻量模型", tab: "model" },
  { key: "DEEPSEEK_PRO_MODEL", label: "DEEPSEEK_PRO_MODEL", type: "text", description: "用于深度分析场景（如技术分析、交易计划生成）的高性能模型", tab: "model" },
  { key: "THINKING_MODE", label: "THINKING_MODE", type: "text", description: "思考模式：auto=按场景自动路由 / fast=全部走Flash(最快) / deep=深度分析强制Pro", tab: "model" },
  // 行情限流
  { key: "LIVE_PRICE_POLL_INTERVAL", label: "LIVE_PRICE_POLL_INTERVAL", type: "number", description: "实时行情轮询间隔（秒）", tab: "ratelimit" },
  { key: "API_RATE_LIMIT", label: "API_RATE_LIMIT", type: "number", description: "API 调用速率上限（次/窗口）", tab: "ratelimit" },
  { key: "API_RATE_WINDOW", label: "API_RATE_WINDOW", type: "number", description: "API 速率限制时间窗口（秒）", tab: "ratelimit" },
  // 数据与交易
  { key: "DB_PATH", label: "DB_PATH", type: "text", tab: "data" },
  { key: "INITIAL_BALANCE", label: "INITIAL_BALANCE", type: "number", description: "每日限修改一次", tab: "data" },
  // 超时与记忆
  { key: "HTTP_TIMEOUT_STOCK", label: "HTTP_TIMEOUT_STOCK", type: "number", description: "股票数据 HTTP 请求超时（秒）", tab: "timeout" },
  { key: "HTTP_TIMEOUT_LLM_CHAT", label: "HTTP_TIMEOUT_LLM_CHAT", type: "number", description: "LLM 普通对话 HTTP 超时（秒）", tab: "timeout" },
  { key: "HTTP_TIMEOUT_LLM_STREAM", label: "HTTP_TIMEOUT_LLM_STREAM", type: "number", description: "LLM 流式响应 HTTP 超时（秒）", tab: "timeout" },
  { key: "MAX_RECENT_MESSAGES", label: "MAX_RECENT_MESSAGES", type: "number", description: "保留最近消息条数", tab: "timeout" },
  { key: "MAX_TOTAL_MESSAGES", label: "MAX_TOTAL_MESSAGES", type: "number", description: "会话最大消息总数", tab: "timeout" },
  { key: "SUMMARY_TRIM_THRESHOLD", label: "SUMMARY_TRIM_THRESHOLD", type: "number", description: "触发摘要压缩的消息数阈值", tab: "timeout" },
];

function getDefaultFormData(): Record<string, string> {
  const data: Record<string, string> = {};
  for (const f of FIELDS) {
    data[f.key] = "";
  }
  return data;
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-surface-secondary/60 border border-border px-4 py-3">
      <p className="text-[11px] text-text-muted mb-1">{label}</p>
      <p className="text-xl font-data text-text-primary tabular-nums">{value}</p>
    </div>
  );
}

function MetricsView({
  loading,
  error,
  data,
}: {
  loading: boolean;
  error: string | null;
  data: ObservabilityOverview | null;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-7 h-7 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
      </div>
    );
  }
  if (error) {
    return <div className="text-sm text-danger py-10 text-center">{error}</div>;
  }
  if (!data) {
    return <div className="text-sm text-text-muted py-10 text-center">暂无运行统计</div>;
  }

  const m = data.metrics || { total_traces: 0, total_sessions: 0, avg_success_rate: 0, total_failed: 0, total_tokens: 0, by_agent: {} };
  const agents = Object.entries(m.by_agent || {});
  const ev = data.evaluation?.intent;
  const traces = data.recent_traces || [];
  const alerts = data.alerts || [];

  return (
    <div className="space-y-6">
      {alerts.length > 0 && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 space-y-1.5">
          {alerts.map((a, i) => (
            <div key={i} className="flex items-start gap-2 text-[12px] text-danger">
              <AlertCircleIcon size={14} className="mt-0.5 flex-shrink-0" />
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatCard label="会话数" value={String(m.total_sessions ?? 0)} />
        <StatCard label="Agent 链路数" value={String(m.total_traces ?? 0)} />
        <StatCard label="平均成功率" value={`${Math.round((m.avg_success_rate ?? 0) * 100)}%`} />
        <StatCard label="累计 Token" value={String(m.total_tokens ?? 0)} />
        <StatCard label="失败次数" value={String(m.total_failed ?? 0)} />
        <StatCard label="审计事件" value={String(data.audit?.recent_events ?? 0)} />
      </div>

      <div className="rounded-xl border border-border bg-surface-secondary/40 p-4">
        <h3 className="text-[13px] font-semibold text-text-secondary mb-3">意图识别评估（确定性安全网）</h3>
        {ev ? (
          <div className="flex items-center gap-4">
            <div className="text-2xl font-data text-accent tabular-nums">{Math.round((ev.accuracy ?? 0) * 100)}%</div>
            <div className="text-[12px] text-text-muted">
              {ev.correct} / {ev.total} 样本正确
              {ev.failures?.length ? (
                <span className="text-danger">，{ev.failures.length} 个失败</span>
              ) : (
                <span className="text-success">，全部通过</span>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-text-muted">暂无评估数据</p>
        )}
        {ev?.failures && ev.failures.length > 0 && (
          <div className="mt-3 space-y-1">
            {ev.failures.map((f, i) => (
              <div key={i} className="text-[11px] text-text-muted">
                {f.input}：期望 {f.expected} → 实际 {f.got}
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className="text-[13px] font-semibold text-text-secondary mb-3">各 Agent 执行明细</h3>
        {agents.length === 0 ? (
          <p className="text-sm text-text-muted">暂无 Agent 执行记录（完成一次对话后生成）</p>
        ) : (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-text-muted border-b border-border">
                <th className="text-left font-medium py-2">Agent</th>
                <th className="text-right font-medium py-2">调用</th>
                <th className="text-right font-medium py-2">成功</th>
                <th className="text-right font-medium py-2">失败</th>
                <th className="text-right font-medium py-2">成功率</th>
                <th className="text-right font-medium py-2">平均耗时</th>
                <th className="text-right font-medium py-2">Token</th>
              </tr>
            </thead>
            <tbody>
              {agents.map(([key, s]) => {
                const rate = s.total ? `${Math.round((s.ok / s.total) * 100)}%` : "-";
                return (
                  <tr key={key} className="border-b border-border/50 text-text-primary">
                    <td className="py-2 font-data">{key}</td>
                    <td className="text-right tabular-nums">{s.total}</td>
                    <td className="text-right tabular-nums text-success">{s.ok}</td>
                    <td className="text-right tabular-nums text-danger">{s.failed}</td>
                    <td className="text-right tabular-nums">{rate}</td>
                    <td className="text-right tabular-nums">{s.avg_ms}ms</td>
                    <td className="text-right tabular-nums">{s.tokens}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div>
        <h3 className="text-[13px] font-semibold text-text-secondary mb-3">近期执行链路（Trace）</h3>
        {traces.length === 0 ? (
          <p className="text-sm text-text-muted">暂无 trace 记录</p>
        ) : (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-text-muted border-b border-border">
                <th className="text-left font-medium py-2">Agent</th>
                <th className="text-left font-medium py-2">意图</th>
                <th className="text-left font-medium py-2">状态</th>
                <th className="text-right font-medium py-2">耗时</th>
                <th className="text-right font-medium py-2">Token</th>
                <th className="text-right font-medium py-2">时间</th>
              </tr>
            </thead>
            <tbody>
              {traces.map((t) => (
                <tr key={t.id} className="border-b border-border/50 text-text-primary">
                  <td className="py-2 font-data">{t.agent}</td>
                  <td className="text-text-muted">{t.intent || "-"}</td>
                  <td className={t.status === "ok" ? "text-success" : "text-danger"}>{t.status}</td>
                  <td className="text-right tabular-nums">{t.duration_ms}ms</td>
                  <td className="text-right tabular-nums">{t.token_used}</td>
                  <td className="text-right tabular-nums text-text-muted">{t.created_at?.slice(11, 19) ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const AGENT_OPTIONS = [
  { key: "chief_strategist", label: "首席策略" },
  { key: "quant_researcher", label: "量化分析" },
  { key: "market_intelligence", label: "市场情报" },
  { key: "trade_executor", label: "交易执行" },
  { key: "portfolio_monitor", label: "持仓风控" },
];

function SchedulerView() {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [form, setForm] = useState({
    name: "", agent_key: "chief_strategist", prompt: "",
    schedule_type: "interval", interval_seconds: "3600", daily_time: "09:00",
  });

  const load = async () => {
    setLoading(true);
    setError(null);
    try { const r = await api.scheduler.list(); setTasks(r.tasks || []); }
    catch (e: any) { setError(e.message || "加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!form.name.trim() || !form.prompt.trim()) { setError("请填写任务名称与任务内容"); return; }
    try {
      await api.scheduler.create({
        name: form.name.trim(),
        agent_key: form.agent_key,
        prompt: form.prompt.trim(),
        schedule_type: form.schedule_type,
        interval_seconds: Number(form.interval_seconds) || 3600,
        daily_time: form.schedule_type === "daily" ? form.daily_time : undefined,
      });
      setShowForm(false);
      setForm({ ...form, name: "", prompt: "" });
      await load();
    } catch (e: any) { setError(e.message || "创建失败"); }
  };

  const toggle = async (t: ScheduledTask) => {
    try { await api.scheduler.update(t.id, { status: t.status === "active" ? "paused" : "active" }); await load(); }
    catch (e: any) { setError(e.message || "操作失败"); }
  };
  const remove = async (id: number) => {
    try { await api.scheduler.delete(id); await load(); }
    catch (e: any) { setError(e.message || "删除失败"); }
  };
  const runNow = async (id: number) => {
    setRunningId(id);
    try { await api.scheduler.run(id); }
    catch (e: any) { setError(e.message || "执行失败"); }
    finally { setRunningId(null); await load(); }
  };

  const scheduleDesc = (t: ScheduledTask) =>
    t.schedule_type === "daily" ? `每天 ${t.daily_time || "—"}` : `每 ${t.interval_seconds} 秒`;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-secondary">Agent 可制定并自动调度的计划任务，支持周期执行与每日定点执行。</p>
        <button onClick={() => setShowForm((v) => !v)} className="btn btn-primary px-4 py-2 text-[12px] rounded-xl">
          {showForm ? "收起" : "新建任务"}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertCircleIcon size={16} />{error}
        </div>
      )}

      {showForm && (
        <div className="rounded-xl border border-border bg-surface-secondary/40 p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[12px] text-text-secondary mb-1.5">任务名称</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="开盘前市场简报"
                className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent" />
            </div>
            <div>
              <label className="block text-[12px] text-text-secondary mb-1.5">执行 Agent</label>
              <select value={form.agent_key} onChange={(e) => setForm({ ...form, agent_key: e.target.value })}
                className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent">
                {AGENT_OPTIONS.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
              </select>
            </div>
          </div>
          <div>
            <label className="block text-[12px] text-text-secondary mb-1.5">任务内容（提示词）</label>
            <textarea value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} rows={3}
              placeholder="例如：分析当前持仓的整体风险，并给出风控建议"
              className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent resize-none" />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-[12px] text-text-secondary mb-1.5">调度类型</label>
              <select value={form.schedule_type} onChange={(e) => setForm({ ...form, schedule_type: e.target.value })}
                className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent">
                <option value="interval">周期（秒）</option>
                <option value="daily">每日定点</option>
              </select>
            </div>
            {form.schedule_type === "interval" ? (
              <div>
                <label className="block text-[12px] text-text-secondary mb-1.5">间隔（秒）</label>
                <input type="number" value={form.interval_seconds} onChange={(e) => setForm({ ...form, interval_seconds: e.target.value })}
                  min="10" className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent font-data" />
              </div>
            ) : (
              <div>
                <label className="block text-[12px] text-text-secondary mb-1.5">时间（HH:MM）</label>
                <input value={form.daily_time} onChange={(e) => setForm({ ...form, daily_time: e.target.value })}
                  placeholder="09:00" className="w-full px-3 py-2 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary focus:outline-none focus:border-accent font-data" />
              </div>
            )}
            <div className="flex items-end">
              <button onClick={create} className="btn btn-primary px-4 py-2 text-[12px] rounded-xl w-full">创建</button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
        </div>
      ) : tasks.length === 0 ? (
        <p className="text-sm text-text-muted py-8 text-center">暂无定时任务</p>
      ) : (
        <div className="space-y-3">
          {tasks.map((t) => (
            <div key={t.id} className="rounded-xl border border-border bg-surface-secondary/40 p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] font-semibold text-text-primary">{t.name}</span>
                <span className={`chip ${t.status === "active" ? "bg-success-soft text-success border-success/20" : "bg-elevated text-text-muted border-border"}`}>
                  {t.status === "active" ? "运行中" : "已暂停"}
                </span>
                <span className="text-[11px] text-text-muted">{AGENT_OPTIONS.find((a) => a.key === t.agent_key)?.label || t.agent_key}</span>
                <div className="ml-auto flex gap-2">
                  <button onClick={() => runNow(t.id)} disabled={runningId === t.id}
                    className="btn btn-secondary px-3 py-1.5 text-[11px] rounded-lg disabled:opacity-50">
                    {runningId === t.id ? "执行中..." : "立即执行"}
                  </button>
                  <button onClick={() => toggle(t)} className="btn btn-secondary px-3 py-1.5 text-[11px] rounded-lg">
                    {t.status === "active" ? "暂停" : "恢复"}
                  </button>
                  <button onClick={() => remove(t.id)} className="btn btn-ghost px-3 py-1.5 text-[11px] rounded-lg text-danger">删除</button>
                </div>
              </div>
              <p className="text-[12px] text-text-muted mt-2 line-clamp-1">{t.prompt}</p>
              <div className="flex items-center gap-4 mt-2 text-[11px] text-text-muted font-data">
                <span>{scheduleDesc(t)}</span>
                {t.next_run_at && <span>下次：{t.next_run_at?.slice(0, 19)?.replace("T", " ")}</span>}
                {t.last_run_at && <span>上次：{t.last_run_at?.slice(0, 19)?.replace("T", " ")}</span>}
              </div>
              {t.last_result && (
                <div className="mt-2 rounded-lg bg-input-bg border border-border px-3 py-2 text-[11px] text-text-secondary leading-relaxed max-h-20 overflow-y-auto whitespace-pre-wrap">
                  {t.last_result}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ProfileView({ onChanged }: { onChanged?: () => void }) {
  const [nickname, setNickname] = useState("");
  const [avatar, setAvatar] = useState("blue");
  const [questions, setQuestions] = useState<RiskQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [riskLevel, setRiskLevel] = useState("balanced");
  const [riskScore, setRiskScore] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [riskSaving, setRiskSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.profile.get(), api.profile.riskQuestions()])
      .then(([p, q]) => {
        if (cancelled) return;
        if (p.profile) {
          setNickname(p.profile.nickname || "");
          setAvatar(p.profile.avatar || "blue");
          setRiskLevel(p.profile.risk_level || "balanced");
          setRiskScore(p.profile.risk_score || 0);
        }
        setQuestions(q.questions || []);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      setAvatar(await compressImage(f));
      setMessage(null);
    } catch {
      setMessage({ type: "error", text: "头像上传失败，请更换图片重试" });
    }
  };

  const saveProfile = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await api.profile.update({ nickname: nickname.trim() || "投资者", avatar });
      setMessage({ type: "success", text: "个人资料已保存" });
      onChanged?.();
    } catch (e: any) {
      setMessage({ type: "error", text: e?.message || "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  const saveRisk = async () => {
    if (Object.keys(answers).length === 0) {
      setMessage({ type: "error", text: "请先完成风险评估问卷" });
      return;
    }
    setRiskSaving(true);
    setMessage(null);
    try {
      const r = await api.profile.riskAssessment(answers);
      await api.profile.update({ risk_level: r.risk_level, risk_score: r.risk_score });
      setRiskLevel(r.risk_level);
      setRiskScore(r.risk_score);
      setMessage({ type: "success", text: "风险评估已更新" });
      onChanged?.();
    } catch (e: any) {
      setMessage({ type: "error", text: e?.message || "保存失败" });
    } finally {
      setRiskSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-7 h-7 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="space-y-5">
        <h3 className="text-[13px] font-semibold text-text-secondary">个人资料</h3>
        <div>
          <label className="block text-[12px] text-text-muted mb-1.5">昵称</label>
          <input
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="投资者"
            className="w-full max-w-sm px-4 py-2.5 text-[13px] rounded-lg bg-input-bg border border-input-border text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
          />
        </div>
        <div>
          <label className="block text-[12px] text-text-muted mb-2">头像</label>
          <div className="flex items-center gap-3 flex-wrap">
            {avatar.startsWith("data:") && (
              <button
                onClick={() => fileRef.current?.click()}
                className="w-12 h-12 rounded-full overflow-hidden ring-2 ring-accent transition-transform hover:scale-105"
                title="重新上传头像"
              >
                <img src={avatar} alt="头像" className="w-full h-full object-cover" />
              </button>
            )}
            {AVATAR_OPTIONS.map((a) => (
              <button
                key={a.id}
                onClick={() => setAvatar(a.id)}
                title={a.id}
                className={`w-12 h-12 rounded-full flex items-center justify-center text-white font-semibold text-lg transition-all ${
                  avatar === a.id ? "ring-2 ring-accent scale-110" : "opacity-70 hover:opacity-100 hover:scale-105"
                }`}
                style={{ background: `linear-gradient(135deg, ${a.from}, ${a.to})` }}
              >
                {(nickname || "投").slice(0, 1)}
              </button>
            ))}
            <button
              onClick={() => fileRef.current?.click()}
              className="w-12 h-12 rounded-full border-2 border-dashed border-border flex items-center justify-center text-text-muted hover:border-accent hover:text-accent transition-colors"
              title="上传头像"
            >
              <UploadIcon size={16} />
            </button>
            <input ref={fileRef} type="file" accept="image/*" onChange={onFile} className="hidden" />
          </div>
        </div>
        <button
          onClick={saveProfile}
          disabled={saving}
          className="btn btn-primary px-5 py-2 text-[13px] rounded-xl disabled:opacity-60"
        >
          {saving ? "保存中..." : "保存个人资料"}
        </button>
      </div>

      <div className="border-t border-border pt-6 space-y-5">
        <div className="flex items-center justify-between">
          <h3 className="text-[13px] font-semibold text-text-secondary">风险评估</h3>
          <span className="chip bg-accent/10 text-accent border-accent/20">
            当前：{RISK_LABELS[riskLevel] || "均衡型"}（{riskScore}分）
          </span>
        </div>
        {questions.length === 0 ? (
          <p className="text-sm text-text-muted">暂无问卷</p>
        ) : (
          <div className="space-y-4">
            {questions.map((q, qi) => (
              <div key={q.id} className="rounded-xl border border-border bg-surface-secondary/40 p-4">
                <p className="text-[13px] font-medium text-text-primary mb-3">{qi + 1}. {q.question}</p>
                <div className="flex flex-wrap gap-2">
                  {q.options.map((opt, i) => {
                    const active = answers[q.id] === i;
                    return (
                      <button
                        key={i}
                        onClick={() => setAnswers((prev) => ({ ...prev, [q.id]: i }))}
                        className={`px-3 py-1.5 text-[12px] rounded-lg border transition-colors ${
                          active
                            ? "bg-accent/15 border-accent/40 text-accent"
                            : "bg-input-bg border-input-border text-text-secondary hover:border-accent/40"
                        }`}
                      >
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
            <button
              onClick={saveRisk}
              disabled={riskSaving}
              className="btn btn-primary px-5 py-2 text-[13px] rounded-xl disabled:opacity-60"
            >
              {riskSaving ? "保存中..." : "保存风险评估"}
            </button>
          </div>
        )}
      </div>

      {message && (
        <div className={`flex items-center gap-2 text-sm ${message.type === "success" ? "text-success" : "text-danger"}`}>
          {message.type === "success" ? <CheckCircleIcon size={16} /> : <AlertCircleIcon size={16} />}
          {message.text}
        </div>
      )}
    </div>
  );
}

export default function SettingsDialog({ open, onClose, onProfileChanged }: SettingsDialogProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("profile");
  const [formData, setFormData] = useState<Record<string, string>>(getDefaultFormData);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [hasFetched, setHasFetched] = useState(false);
  const [showSecrets, setShowSecrets] = useState(false);
  const [metrics, setMetrics] = useState<ObservabilityOverview | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState<string | null>(null);

  // 对话框打开时拉取设置
  useEffect(() => {
    if (!open) {
      setHasFetched(false);
      setMessage(null);
      return;
    }
    if (hasFetched) return;

    let cancelled = false;
    setLoading(true);
    setMessage(null);
    api.settings.get()
      .then((res) => {
        if (cancelled) return;
        const mapped: Record<string, string> = getDefaultFormData();
        if (res.categories) {
          // 从后端分组结构中提取配置值
          for (const items of Object.values(res.categories)) {
            if (Array.isArray(items)) {
              for (const item of items) {
                if (item.key && item.value !== undefined) {
                  mapped[item.key] = String(item.value);
                }
              }
            }
          }
        }
        setFormData(mapped);
        setHasFetched(true);
      })
      .catch((err) => {
        if (cancelled) return;
        setMessage({ type: "error", text: err.message || "加载设置失败" });
        setHasFetched(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, hasFetched]);

  const handleChange = (key: string, value: string) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
    setMessage(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const res = await api.settings.update(formData);
      setMessage({ type: "success", text: res.message || "保存成功" });
    } catch (err: any) {
      setMessage({ type: "error", text: err.message || "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  // 运行统计标签：切换到时拉取一次（每次进入都刷新）
  useEffect(() => {
    if (!open || activeTab !== "metrics") return;
    let cancelled = false;
    setMetricsLoading(true);
    setMetricsError(null);
    api.observabilityOverview()
      .then((res) => {
        if (!cancelled) setMetrics(res);
      })
      .catch((err) => {
        if (!cancelled) setMetricsError(err.message || "加载运行统计失败");
      })
      .finally(() => {
        if (!cancelled) setMetricsLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, activeTab]);

  if (!open) return null;

  const activeFields = FIELDS.filter((f) => f.tab === activeTab);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass-strong rounded-2xl border border-border shadow-2xl w-full max-w-5xl mx-6"
        style={{ boxShadow: "0 24px 60px rgba(0,0,0,0.45)" }}
        onClick={(e) => e.stopPropagation()}
      >
{/* 标题栏 */}
        <div className="flex items-center justify-between px-8 py-5 border-b border-border">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold text-text-primary">系统设置</h2>
          </div>
          <button
            onClick={onClose}
            className="py-1.5 px-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-elevated transition-colors inline-flex items-center"
          >
            <XIcon size={20} />
          </button>
        </div>

{/* 标签页 */}
        <div className="flex border-b border-border px-8 gap-4">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`relative px-5 py-3.5 text-[14px] font-medium transition-colors -mb-px ${
                activeTab === tab.key
                  ? "text-accent"
                  : "text-text-muted hover:text-text-secondary"
              }`}
            >
              {tab.label}
              {activeTab === tab.key && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-accent rounded-full" />
              )}
            </button>
          ))}
        </div>

{/* 设置内容 */}
        <div className="px-10 py-7 max-h-[600px] overflow-y-auto">


          {activeTab === "profile" ? (
            <ProfileView onChanged={onProfileChanged} />
          ) : activeTab === "metrics" ? (
            <MetricsView loading={metricsLoading} error={metricsError} data={metrics} />
          ) : activeTab === "scheduler" ? (
            <SchedulerView />
          ) : loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="w-7 h-7 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            </div>
          ) : (
            <div className="space-y-7">
              {activeFields.map((field) => (
                <div key={field.key} className="space-y-2.5">
                  <label className="block text-[13px] font-medium text-text-secondary">
                    {field.label}
                  </label>
                  {field.type === "password" ? (
                    <div className="relative">
                      <input
                        type={showSecrets ? "text" : "password"}
                        value={formData[field.key]}
                        onChange={(e) => handleChange(field.key, e.target.value)}
                        placeholder={PASSWORD_PLACEHOLDER}
                        className="w-full px-4 py-3 pr-16 text-[13px] rounded-xl bg-input-bg border border-input-border text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-all font-mono"
                      />
                      <button
                        type="button"
                        onClick={() => setShowSecrets((v) => !v)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-[11px] text-text-muted hover:text-text-primary rounded-md hover:bg-elevated transition-colors"
                      >
                        {showSecrets ? "隐藏" : "显示"}
                      </button>
                    </div>
                  ) : (
                    <input
                      type={field.type}
                      value={formData[field.key]}
                      onChange={(e) => handleChange(field.key, e.target.value)}
                      placeholder={field.type === "number" ? "0" : ""}
                      className={`w-full px-4 py-3 text-[13px] rounded-xl bg-input-bg border border-input-border text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-all ${
                        field.type === "number" ? "font-data" : ""
                      }`}
                    />
                  )}
                  {field.description && (
                    <p className="text-[11px] text-text-muted ml-1 leading-relaxed">{field.description}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

{/* 底部操作 */}
        <div className="flex items-center justify-between px-8 py-5 border-t border-border">
          <div>
            {message && (
              <div
                className={`flex items-center gap-2 text-sm animate-fade-in ${
                  message.type === "success" ? "text-success" : "text-danger"
                }`}
              >
                {message.type === "success" ? (
                  <CheckCircleIcon size={16} />
                ) : (
                  <AlertCircleIcon size={16} />
                )}
                {message.text}
              </div>
            )}
          </div>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="btn btn-secondary px-5 py-2.5 text-sm rounded-xl"
            >
              取消
            </button>
            {activeTab !== "profile" && activeTab !== "metrics" && activeTab !== "scheduler" && (
              <button
                onClick={handleSave}
                disabled={saving || loading}
                className="btn btn-primary px-5 py-2.5 text-sm rounded-xl"
              >
                {saving && (
                  <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                )}
                {saving ? "保存中..." : "保存设置"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

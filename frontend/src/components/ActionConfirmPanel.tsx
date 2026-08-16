import { AlertCircleIcon, CheckCircleIcon, ListIcon, XIcon } from "./Icon";
import { useChatStore } from "../stores/chatStore";

const TYPE_LABEL: Record<string, { label: string; color: string; desc: string }> = {
  watchlist_add: { label: "添加自选", color: "#10B981", desc: "将该股票加入自选股列表" },
  watchlist_remove: { label: "移除自选", color: "#F59E0B", desc: "将该股票从自选股列表移除" },
  cancel_order: { label: "撤销委托", color: "#EF4444", desc: "撤销选中的挂单委托" },
};

interface Props {
  action: {
    type: string;
    message: string;
    data?: { symbol?: string; name?: string; order_ids?: string[] };
  };
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ActionConfirmPanel({ action, onConfirm, onCancel }: Props) {
  const actionPending = useChatStore((s) => s.actionPending);
  const info = TYPE_LABEL[action.type] || { label: action.type, color: "#60a5fa", desc: "" };
  const symbol = action.data?.symbol;
  const name = action.data?.name;
  const orderCount = action.data?.order_ids?.length;

  return (
    <div className="rounded-xl border border-border bg-surface-secondary shadow-2xl overflow-hidden animate-scale-in">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 bg-elevated border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="chip" style={{ color: info.color, borderColor: info.color + "30", backgroundColor: info.color + "0a" }}>{info.label}</span>
          {name && symbol && <span className="text-[13px] font-bold text-text-primary truncate">{name}({symbol})</span>}
          {orderCount != null && <span className="text-[12px] text-text-secondary font-data">{orderCount} 笔</span>}
        </div>
        <span className="flex items-center gap-1 text-[11px] text-text-muted"><ListIcon size={12} />待确认</span>
      </div>
      <div className="px-4 py-3 bg-bg border-b border-border">
        <p className="text-[12px] text-text-secondary leading-relaxed">{action.message}</p>
        {info.desc && <p className="text-[11px] text-text-muted mt-1">{info.desc}</p>}
      </div>
      <div className="flex items-center justify-center gap-2 p-3 bg-elevated">
        <button onClick={onCancel} disabled={actionPending} className="btn px-5 h-8 text-[12px] text-text-secondary bg-input-bg border border-input-border hover:bg-elevated-hover disabled:opacity-50">
          <XIcon size={12} /> 取消
        </button>
        <button onClick={onConfirm} disabled={actionPending}
          className="btn px-5 h-8 text-[12px] font-bold text-white disabled:opacity-50"
          style={{ background: info.color, opacity: 0.92 }}>
          {actionPending ? <span className="btn-spinner" /> : <CheckCircleIcon size={12} />} 确认执行
        </button>
      </div>
      <div className="px-4 py-1.5 text-[11px] text-text-muted flex items-center gap-1 border-t border-border bg-bg">
        <AlertCircleIcon size={11} /> 确认后将直接修改您的账户数据
      </div>
    </div>
  );
}

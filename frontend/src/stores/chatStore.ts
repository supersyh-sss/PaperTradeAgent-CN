import { create } from "zustand";
import { api } from "../api/client";
import type { AgentLog, ConversationItem, WatchlistItem, PortfolioData, Message } from "../api/client";
import { readSSE } from "../api/sse";

interface ActionRequired {
  type: string;
  message: string;
  trade_plan?: {
    symbol: string;
    name: string;
    side: string;
    quantity: number;
    price: number;
    prev_close?: number;
    is_trading_time: boolean;
    risk_level: string;
    suggested_price: number;
    suggested_quantity: number;
    current_price: number;
    smart_pricing_note: string;
    estimated_amount: number;
    force_mode_available: boolean;
  };
  data?: {
    symbol?: string;
    name?: string;
    order_ids?: string[];
    [key: string]: any;
  };
}

interface ChatState {
  messages: Message[];
  isLoading: boolean;
  // Single streaming agent (replaces currentAgents array)
  streamingAgent: AgentLog | null;
  hasAgentRunThisRound: boolean;
  activeSymbol: string | null;
  activeName: string | null;
  watchlist: WatchlistItem[];
  portfolio: PortfolioData | null;
  conversations: ConversationItem[];
  conversationId: string;
  activeStockDetail: { symbol: string; name: string } | null;
  actionRequired: ActionRequired | null;
  actionPending: boolean;

  sendMessage: (text: string) => void;
  stopGeneration: () => void;
  sendTradeAction: (action: string, tradePlan: any, forceMode?: boolean) => Promise<void>;
  sendAction: (action: string, actionType: string, data: any) => Promise<void>;
  confirmTrade: (price: number, quantity: number, forceMode: boolean) => void;
  cancelTrade: () => void;
  confirmGenericAction: () => void;
  cancelGenericAction: () => void;
  loadPortfolio: () => Promise<void>;
  loadWatchlist: () => Promise<void>;
  addToWatchlist: (symbol: string, name: string) => Promise<void>;
  removeFromWatchlist: (symbol: string) => Promise<void>;
  openStockDetail: (symbol: string, name: string) => void;
  closeStockDetail: () => void;
  loadConversations: () => Promise<void>;
  loadConversation: (convId: string) => Promise<void>;
  newConversation: () => void;
  setConversationId: (id: string) => void;
  cancelOrder: (orderId: string) => Promise<void>;
  generateReport: (intent: string) => Promise<void>;
  activeOrders: any[];
  loadActiveOrders: () => Promise<void>;
  orderHistory: any[];
  loadOrderHistory: () => Promise<void>;
  feedback: Record<string, "up" | "down">;
  loadFeedback: () => Promise<void>;
  sendFeedback: (agent: string, content: string, feedback: "up" | "down") => Promise<void>;
  connectSessionStream: () => void;
}

const AGENT_INFO: Record<string, { name_cn: string; color: string }> = {
  chief_strategist: { name_cn: "首席策略", color: "#60a5fa" },
  quant_researcher: { name_cn: "量化分析", color: "#a78bfa" },
  market_intelligence: { name_cn: "市场情报", color: "#34d399" },
  trade_executor: { name_cn: "交易执行", color: "#f87171" },
  portfolio_monitor: { name_cn: "持仓风控", color: "#fbbf24" },
  response_generator: { name_cn: "综合分析", color: "#fb923c" },
};

function generateConvId() {
  return `conv_${new Date().toISOString().replace(/[:.]/g, "_")}_${Math.random().toString(36).slice(2, 6)}`;
}

export function hashContent(agent: string, content: string): string {
  const s = `${agent}\n${content}`;
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = (h * 33 + s.charCodeAt(i)) >>> 0;
  return String(h);
}

let activeController: AbortController | null = null;
let sessionStream: EventSource | null = null;

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  isLoading: false,
  streamingAgent: null,
  hasAgentRunThisRound: false,
  activeSymbol: null,
  activeName: null,
  watchlist: [],
  portfolio: null,
  conversations: [],
  conversationId: generateConvId(),
  activeStockDetail: null,
  actionRequired: null,
  actionPending: false,
  activeOrders: [],
  orderHistory: [],
  feedback: {},
  cancelOrder: async (orderId: string) => {
    if (get().actionPending) return;
    set({ actionPending: true });
    try {
      const res = await api.cancelOrder(orderId);
      if (res.success) {
        set((s) => ({
          messages: [...s.messages, { role: "assistant", content: `订单已撤销：${res.message}`, timestamp: new Date().toISOString() }],
          activeOrders: s.activeOrders.filter((o: any) => o.order_id !== orderId),
        }));
        get().loadOrderHistory().catch(() => {});
      } else {
        set((s) => ({
          messages: [...s.messages, { role: "assistant", content: `撤单失败：${res.message}`, timestamp: new Date().toISOString() }],
        }));
      }
    } catch (err: any) {
      set((s) => ({
        messages: [...s.messages, { role: "assistant", content: `撤单请求失败：${err?.message || "网络异常"}`, timestamp: new Date().toISOString() }],
      }));
    } finally {
      set({ actionPending: false });
    }
  },
  generateReport: async (intent: string) => {
    if (get().actionPending) return;
    const { conversationId } = get();
    set({ actionPending: true, isLoading: true });
    try {
      const res = await api.generateReport(conversationId, intent);
      if (res.success && res.report) {
        set((s) => ({
          messages: [...s.messages, {
            role: "assistant",
            content: res.report,
            timestamp: new Date().toISOString(),
            metadata: { intent, needs_report: true, report_generated: true },
          }],
        }));
      }
    } catch (err: any) {
      set((s) => ({
        messages: [...s.messages, {
          role: "assistant",
          content: `报告生成失败：${err?.message || "网络异常"}`,
          timestamp: new Date().toISOString(),
        }],
      }));
    } finally {
      set({ actionPending: false, isLoading: false });
    }
  },
  loadActiveOrders: async () => {
    try {
      const res = await api.getActiveOrders();
      set({ activeOrders: res.data || [] });
    } catch {}
  },
  loadOrderHistory: async () => {
    try {
      const res = await api.getOrderHistory();
      set({ orderHistory: res.data || [] });
    } catch {}
  },
  loadFeedback: async () => {
    try {
      const res = await api.getFeedback();
      const map: Record<string, "up" | "down"> = {};
      (res.data || []).forEach((f) => { map[f.content_hash] = f.feedback; });
      set({ feedback: map });
    } catch {}
  },
  sendFeedback: async (agent: string, content: string, feedback: "up" | "down") => {
    const h = hashContent(agent, content);
    const togglingOff = get().feedback[h] === feedback;
    set((s) => {
      const next = { ...s.feedback };
      if (togglingOff) delete next[h];
      else next[h] = feedback;
      return { feedback: next };
    });
    try {
      const { conversationId } = get();
      await api.sendFeedback(agent, content, togglingOff ? "none" : feedback, conversationId);
    } catch {}
  },

  sendMessage: (text: string) => {
    const { conversationId } = get();
    // 未初始化时自动生成会话ID
    const convId = conversationId || generateConvId();
    if (!conversationId) {
      set({ conversationId: convId });
    }

    const userMsg: Message = {
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };

    set((s) => ({
      messages: [...s.messages, userMsg],
      isLoading: true,
      streamingAgent: null,
      hasAgentRunThisRound: false,
      actionRequired: null,
    }));

    // 建立 SSE 流式连接
    const url = "/api/chat/stream";
    const token = import.meta.env.VITE_TEST_TOKEN || "Bearer mvp_test_token_2026";
    const controller = new AbortController();
    activeController = controller;

    fetch(url, {
      method: "POST",
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Authorization: token,
      },
      body: JSON.stringify({
        message: text,
        conversation_id: convId,
        current_time: new Date().toISOString(),
      }),
    })
      .then(async (resp) => {
        if (!resp.ok) {
          if (resp.status === 429) throw new Error("请求过于频繁，请稍后重试");
          throw new Error(`HTTP ${resp.status}`);
        }
        return readSSE(resp);
      })
      .then(async (events) => {
        // 逐条处理 SSE 事件
        for await (const evt of events) {
          try {
            const data = JSON.parse(evt.data);
            switch (data.type) {
              case "agent_log_start": {
                const info = AGENT_INFO[data.agent] || {
                  name_cn: data.name_cn || data.agent,
                  color: data.color || "#60a5fa",
                };
                const agent: AgentLog = {
                  agent: data.agent,
                  emoji: data.emoji || "",
                  name_cn: info.name_cn,
                  color: info.color,
                  content: "",
                  timestamp: data.timestamp || new Date().toISOString(),
                };
                set({ streamingAgent: agent, hasAgentRunThisRound: true });
                break;
              }
              case "agent_token": {
                set((s) => {
                  if (!s.streamingAgent) return {};
                  return {
                    streamingAgent: {
                      ...s.streamingAgent,
                      content: s.streamingAgent.content + (data.text || ""),
                    },
                  };
                });
                break;
              }
              case "agent_log_end": {
                set((s) => {
                  if (!s.streamingAgent) return {};
                  const completed: Message = {
                    role: "agent",
                    content: s.streamingAgent.content,
                    timestamp: s.streamingAgent.timestamp,
                    metadata: { agentLog: { ...s.streamingAgent } },
                  };
                  return {
                    messages: [...s.messages, completed],
                    streamingAgent: null,
                  };
                });
                break;
              }
              case "done": {
                // 构建助手消息
                const responseText = data.response || "";
                const intent = data.intent || "chat";
                const needsReport = data.needs_report !== false && intent !== "chat" && intent !== "watchlist";

                // 后端已统一返回 action_required（trade_confirm / watchlist_add / watchlist_remove / cancel_order）
                const actionRequired: ActionRequired | null = data.action_required || null;

                const assistantMsg: Message = {
                  role: "assistant",
                  content: responseText,
                  timestamp: new Date().toISOString(),
                  metadata: {
                    intent,
                    needs_report: needsReport,
                    suggest_report: !!data.suggest_report,
                    active_symbol: data.active_symbol,
                    active_name: data.active_name,
                    trade_plan: data.trade_plan,
                    portfolio_summary: data.portfolio_summary,
                    plan: data.plan,
                  },
                };

                set((s) => ({
                  messages: [...s.messages, assistantMsg],
                  isLoading: false,
                  streamingAgent: null,
                  activeSymbol: data.active_symbol || null,
                  activeName: data.active_name || null,
                  actionRequired,
                  portfolio: data.portfolio_summary || s.portfolio,
                }));

                // 后台刷新持仓和自选
                get().loadPortfolio().catch(() => {});
                get().loadWatchlist().catch(() => {});
                get().loadConversations().catch(() => {});
                break;
              }
              case "error": {
                set((s) => ({
                  messages: [
                    ...s.messages,
                    {
                      role: "assistant",
                      content: `系统出错: ${data.message || "未知错误"}`,
                      timestamp: new Date().toISOString(),
                    },
                  ],
                  isLoading: false,
                  streamingAgent: null,
                }));
                break;
              }
            }
          } catch {}
        }
      })
      .catch((err) => {
        if (err?.name === "AbortError") { set({ isLoading: false, streamingAgent: null }); return; }
        const msg = err?.message || "未知错误";
        const friendly = /429|频繁|限流/.test(msg) ? "请求过于频繁，请稍后重试" : `连接异常: ${msg}`;
        set((s) => ({
          messages: [
            ...s.messages,
            {
              role: "assistant",
              content: friendly,
              timestamp: new Date().toISOString(),
            },
          ],
          isLoading: false,
          streamingAgent: null,
        }));
      });
  },

  stopGeneration: () => {
    activeController?.abort();
    activeController = null;
    set({ isLoading: false, streamingAgent: null, hasAgentRunThisRound: false });
  },

  sendTradeAction: async (action: string, tradePlan: any, _forceMode = false) => {
    if (get().actionPending) return;
    set({ actionPending: true });
    const { conversationId } = get();
    try {
      const res = await api.tradeAction(action, "trade", tradePlan, undefined, conversationId);
      if (res.success) {
        const msg = action === "confirm" ? `交易已执行：${res.message}` : `交易已取消`;
        set((s) => ({
          messages: [
            ...s.messages,
            { role: "assistant", content: msg, timestamp: new Date().toISOString() },
          ],
          actionRequired: null,
        }));
        get().loadPortfolio().catch(() => {});
        get().loadActiveOrders().catch(() => {});
        get().loadOrderHistory().catch(() => {});
      } else {
        set((s) => ({
          messages: [
            ...s.messages,
            { role: "assistant", content: `操作失败: ${res.message}`, timestamp: new Date().toISOString() },
          ],
          actionRequired: null,
        }));
      }
    } catch (err: any) {
      set((s) => ({
        messages: [
          ...s.messages,
          { role: "assistant", content: `交易请求失败: ${err?.message || "网络异常"}`, timestamp: new Date().toISOString() },
        ],
        actionRequired: null,
      }));
    } finally {
      set({ actionPending: false });
    }
  },

  sendAction: async (action: string, actionType: string, data: any) => {
    if (get().actionPending) return;
    set({ actionPending: true });
    const { conversationId } = get();
    try {
      const res = await api.tradeAction(action, actionType, undefined, data, conversationId);
      if (res.success) {
        set((s) => ({
          messages: [
            ...s.messages,
            { role: "assistant", content: res.message, timestamp: new Date().toISOString() },
          ],
          actionRequired: null,
        }));
        get().loadPortfolio().catch(() => {});
        get().loadWatchlist().catch(() => {});
        get().loadActiveOrders().catch(() => {});
        get().loadOrderHistory().catch(() => {});
      } else {
        set((s) => ({
          messages: [
            ...s.messages,
            { role: "assistant", content: `操作失败: ${res.message}`, timestamp: new Date().toISOString() },
          ],
          actionRequired: null,
        }));
      }
    } catch (err: any) {
      set((s) => ({
        messages: [
          ...s.messages,
          { role: "assistant", content: `操作请求失败: ${err?.message || "网络异常"}`, timestamp: new Date().toISOString() },
        ],
        actionRequired: null,
      }));
    } finally {
      set({ actionPending: false });
    }
  },

  confirmTrade: (price: number, quantity: number, _forceMode: boolean) => {
    const { actionRequired } = get();
    if (!actionRequired?.trade_plan) return;
    get().sendTradeAction("confirm", {
      ...actionRequired.trade_plan,
      price,
      quantity,
    });
  },

  cancelTrade: () => {
    const { actionRequired } = get();
    if (!actionRequired?.trade_plan) return;
    get().sendTradeAction("cancel", actionRequired.trade_plan);
  },

  confirmGenericAction: () => {
    const { actionRequired } = get();
    if (!actionRequired || actionRequired.type === "trade_confirm") return;
    get().sendAction("confirm", actionRequired.type, actionRequired.data || {});
  },

  cancelGenericAction: () => {
    const { actionRequired } = get();
    if (!actionRequired || actionRequired.type === "trade_confirm") return;
    get().sendAction("cancel", actionRequired.type, actionRequired.data || {});
  },

  loadPortfolio: async () => {
    try {
      const data = await api.portfolio();
      set({ portfolio: data });
    } catch {}
  },

  loadWatchlist: async () => {
    try {
      const data = await api.watchlist();
      set({ watchlist: data.watchlist || [] });
    } catch {}
  },

  addToWatchlist: async (symbol: string, name: string) => {
    await api.addWatchlist(symbol, name);
    await get().loadWatchlist();
  },

  removeFromWatchlist: async (symbol: string) => {
    await api.removeWatchlist(symbol);
    await get().loadWatchlist();
  },

  openStockDetail: (symbol: string, name: string) => {
    set({ activeStockDetail: { symbol, name } });
  },

  closeStockDetail: () => {
    set({ activeStockDetail: null });
  },

  loadConversations: async () => {
    try {
      const data = await api.conversations();
      set({ conversations: data.conversations || [] });
    } catch {}
  },

  loadConversation: async (convId: string) => {
    try {
      const data = await api.getConversation(convId);
      set({
        messages: data.messages || [],
        conversationId: convId,
        isLoading: false,
        streamingAgent: null,
        actionRequired: null,
      });
    } catch {}
  },

  newConversation: () => {
    set({
      messages: [],
      conversationId: generateConvId(),
      isLoading: false,
      streamingAgent: null,
      activeSymbol: null,
      activeName: null,
      activeStockDetail: null,
      actionRequired: null,
    });
  },

  setConversationId: (id: string) => {
    set({ conversationId: id });
  },

  connectSessionStream: () => {
    const convId = get().conversationId;
    if (sessionStream) {
      sessionStream.close();
      sessionStream = null;
    }
    if (!convId) return;
    const es = new EventSource(`/api/session/stream?session_id=${encodeURIComponent(convId)}`);
    sessionStream = es;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "monitor_suggestion" || data.type === "monitor_alert" || data.type === "monitor_warning") {
          const payload = data.data || {};
          const msg: Message = {
            role: "assistant",
            content: payload.message || payload.title || "收到一条风控监控建议",
            timestamp: payload.timestamp || new Date().toISOString(),
            metadata: { monitor: true, monitorData: payload },
          };
          set((s) => ({ messages: [...s.messages, msg] }));
        }
      } catch {}
    };
  },
}));

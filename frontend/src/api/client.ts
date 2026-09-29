const TOKEN = import.meta.env.VITE_TEST_TOKEN || "Bearer mvp_test_token_2026";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: TOKEN,
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ message: "Network error" }));
    throw new Error(err.message || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface AgentLog {
  agent: string;
  emoji: string;
  name_cn: string;
  color: string;
  content: string;
  timestamp: string;
}

export interface ConversationItem {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  role: "user" | "assistant" | "agent";
  content: string;
  timestamp: string;
  metadata?: {
    intent?: string;
    needs_report?: boolean;
    suggest_report?: boolean;
    report_generated?: boolean;
    active_symbol?: string;
    active_name?: string;
    technical_analysis?: any;
    market_intelligence?: any;
    trade_plan?: any;
    order_result?: any;
    portfolio_summary?: any;
    plan?: any;
    agentLog?: AgentLog;
    monitor?: boolean;
    monitorData?: {
      severity?: string;
      title?: string;
      message?: string;
      action?: string;
      symbol?: string;
      name?: string;
      suggested_prompt?: string;
      total_pnl_pct?: number;
      [key: string]: any;
    };
  };
}

export interface ActiveOrder {
  order_id: string;
  symbol: string;
  name: string;
  side: string;
  order_type: string;
  price: number;
  quantity: number;
  filled_qty: number;
  status: string;
  created_at: string;
}

export interface PortfolioData {
  balance: number;
  locked_balance: number;
  available_balance: number;
  total_market_value: number;
  total_cost: number;
  total_pnl: number;
  total_pnl_pct: number;
  total_assets: number;
  positions: Position[];
  position_count: number;
  active_orders?: ActiveOrder[];
}

export interface Position {
  symbol: string;
  name: string;
  quantity: number;
  sellable_quantity: number;
  t1_quantity: number;
  locked_shares: number;
  tradable_quantity: number;
  total_cost: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  pnl: number;
  pnl_pct: number;
  t1_restricted: boolean;
}

export interface WatchlistItem {
  id: number;
  user_id: string;
  symbol: string;
  name: string;
  added_at: string;
  price?: number;
  prev_close?: number;
  change_pct?: number;
}

export interface KlineData {
  symbol: string;
  dates: string[];
  data: number[][];
  volumes: number[];
  ma5: (number | null)[];
  ma10: (number | null)[];
  ma20: (number | null)[];
  ma60: (number | null)[];
  latest_price: number;
  indicators: {
    rsi?: number;
    macd_signal?: string;
    trend?: string;
    volatility?: number;
    support?: number[];
    resistance?: number[];
    ma?: { ma5?: number; ma10?: number; ma20?: number; ma60?: number };
    bollinger?: { upper?: number; middle?: number; lower?: number };
  };
}

export interface ValidateResult {
  valid: boolean;
  symbol: string;
  name: string;
  price: number;
  message: string;
}

export interface TradingStatus {
  status: string;
  is_trading: boolean;
  detail: string;
  auction: {
    is_auction: boolean;
    phase: string;
    can_order: boolean;
    can_cancel: boolean;
    can_match: boolean;
    description: string;
  };
  refresh_interval: number;
  next_trading_day: string;
}

export interface RealtimeData {
  symbol: string;
  name: string;
  price: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  volume: number;
  amount: number;
  turnover: number;
  pe: number;
  pb: number;
}

export interface AgentTraceSummary {
  total: number;
  ok: number;
  failed: number;
  avg_ms: number;
  tokens: number;
}

export interface AgentTrace {
  id: number;
  trace_id: string;
  conversation_id: string;
  user_id: string;
  agent: string;
  intent: string;
  status: string;
  duration_ms: number;
  token_used: number;
  detail: string;
  created_at: string;
}

export interface ObservabilityOverview {
  metrics: {
    total_traces: number;
    total_sessions: number;
    avg_success_rate: number;
    total_failed: number;
    total_tokens: number;
    by_agent: Record<string, AgentTraceSummary>;
  };
  recent_traces: AgentTrace[];
  evaluation: {
    intent: {
      total: number;
      correct: number;
      accuracy: number;
      mode: string;
      failures: { input: string; expected: string; got: string }[];
    };
  };
  audit: { recent_events: number };
  alerts: { level: string; metric: string; message: string }[];
}

export interface UserProfile {
  id: number;
  user_id: string;
  nickname: string;
  avatar: string;
  risk_level: string;
  risk_score: number;
  onboarding_completed: number;
  created_at: string;
  updated_at: string;
}

export interface RiskQuestion {
  id: string;
  question: string;
  options: { label: string; score: number }[];
}

export interface ScheduledTask {
  id: number;
  user_id: string;
  name: string;
  agent_key: string;
  prompt: string;
  schedule_type: "interval" | "daily";
  interval_seconds: number;
  daily_time: string | null;
  status: "active" | "paused";
  last_run_at: string | null;
  next_run_at: string | null;
  last_result: string | null;
  created_at: string;
  updated_at: string;
}

export interface HarnessMetrics {
  session_metrics: {
    sessions: number;
    avg_success_rate: number;
    total_llm_errors: number;
    total_tokens: number;
    latest?: Record<string, unknown> | null;
  };
  agent_traces: {
    total_traces: number;
    total_tokens: number;
    by_agent: Record<string, AgentTraceSummary>;
  };
}

export interface ArenaStatus {
  enabled: boolean;
  configured: boolean;
  agent_id?: string | null;
  daily_assets?: string[];
  check_interval?: number;
  challenges?: { total_open: number; matching_assets: number };
}

export interface ArenaPrediction {
  prediction_id?: string;
  challenge_id?: string;
  event_id?: string;
  asset?: string;
  question?: string;
  direction?: "bullish" | "bearish" | "neutral" | string;
  confidence?: number | null;
  is_correct?: boolean | null;
  score?: number | null;
  result?: string | null;
  created_at?: string;
}

export interface ArenaScorecard {
  agent_id?: string;
  name?: string;
  bio?: string;
  model_provider?: string;
  model_name?: string;
  total_predictions?: number;
  resolved_predictions?: number;
  correct_predictions?: number;
  accuracy_rate?: number | null;
  avg_confidence?: number | null;
  avg_score?: number | null;
  rank?: number | null;
  verified?: boolean;
  assets?: { asset?: string; total?: number; resolved?: number; correct?: number; accuracy_rate?: number | null; avg_score?: number | null }[];
  recent_predictions?: ArenaPrediction[];
}

export interface ArenaCalibration {
  agent_id?: string;
  total?: number;
  buckets?: { bucket?: string; predicted?: number; resolved?: number; correct?: number; empirical?: number | null }[];
}

export interface ArenaRunResult {
  trigger?: string;
  submitted?: number;
  skipped?: number;
  failed?: number;
  assets?: { asset: string; status: string; reason?: string; probabilities?: Record<string, number> }[];
}

export const api = {
  portfolio: () => request<PortfolioData>("/api/portfolio"),

  portfolioHistory: (days = 30) =>
    request<{ history: any[] }>(`/api/portfolio/history?days=${days}`),

  trades: (limit = 50) =>
    request<{ trades: any[] }>(`/api/portfolio/trades?limit=${limit}`),

  watchlist: () =>
    request<{ watchlist: WatchlistItem[]; count: number; max: number }>("/api/watchlist"),

  addWatchlist: (symbol: string, name: string) =>
    request<{ success: boolean; watchlist: WatchlistItem[] }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol, name }),
    }),

  removeWatchlist: (symbol: string) =>
    request<{ success: boolean }>(`/api/watchlist/${symbol}`, {
      method: "DELETE",
    }),

  cancelOrder: (orderId: string) =>
    request<{ success: boolean; message: string; order: any }>("/api/trade/cancel", {
      method: "POST",
      body: JSON.stringify({ order_id: orderId }),
    }),

  getActiveOrders: () =>
    request<{ data: any[]; count: number }>("/api/trade/orders/active"),

  getOrderHistory: () =>
    request<{ data: any[]; locked_balance: number; locked_shares: Record<string, number> }>(
      "/api/trade/orders?status=FILLED,CANCELLED,REJECTED"
    ),

  conversations: () =>
    request<{ conversations: ConversationItem[] }>("/api/chat/conversations"),

  getConversation: (convId: string) =>
    request<{ messages: any[]; conversation_id: string }>(`/api/chat/conversations/${convId}`),

  deleteConversation: (convId: string) =>
    request<{ success: boolean }>(`/api/chat/conversations/${convId}`, { method: "DELETE" }),

  health: () => request<{ status: string; trading: TradingStatus }>("/api/health"),

  harnessMetrics: () => request<HarnessMetrics>("/api/harness/metrics"),

  observabilityOverview: () => request<ObservabilityOverview>("/api/observability/overview"),

  observabilityTraces: (limit = 100, agent?: string) =>
    request<{ total: number; traces: AgentTrace[] }>(
      `/api/observability/traces?limit=${limit}${agent ? `&agent=${encodeURIComponent(agent)}` : ""}`
    ),

  profile: {
    get: () => request<{ profile: UserProfile | null; onboarding_completed: boolean }>("/api/profile"),
    update: (data: Record<string, unknown>) =>
      request<{ success: boolean }>("/api/profile", { method: "PUT", body: JSON.stringify(data) }),
    riskQuestions: () => request<{ questions: RiskQuestion[] }>("/api/profile/risk-questions"),
    riskAssessment: (answers: Record<string, number>) =>
      request<{ risk_score: number; risk_level: string; risk_label: string }>("/api/profile/risk-assessment", {
        method: "POST", body: JSON.stringify({ answers }),
      }),
  },

  tradeAction: (action: string, actionType: string, tradePlan: any, data: any, conversationId?: string) =>
    request<{ success: boolean; message: string; action: string; order_id?: string }>("/api/chat/trade-action", {
      method: "POST",
      body: JSON.stringify({ action, action_type: actionType, trade_plan: tradePlan, data, conversation_id: conversationId }),
    }),

  generateReport: (conversationId: string, intent: string) =>
    request<{ success: boolean; report: string }>("/api/chat/report", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, intent }),
    }),

  validateStock: (query: string) =>
    request<ValidateResult>("/api/watchlist/validate", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  getKline: (symbol: string, days = 360) =>
    request<KlineData>(`/api/stocks/${symbol}/kline?days=${days}`),

  getRealtime: (symbol: string) =>
    request<RealtimeData>(`/api/stocks/${symbol}/realtime`),

  searchStocks: (q: string, limit = 8) =>
    request<{ query: string; results: { code: string; name: string; exchange: string; board: string }[]; total_db: number; db_date: string }>(`/api/stocks/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  getSymbolOrders: (symbol: string) =>
    request<{ data: any[]; symbol: string }>(`/api/trade/orders/symbol/${symbol}`),

  placeOrder: (symbol: string, name: string, side: string, orderType: string, quantity: number, price?: number) =>
    request<{success: boolean; order_id: string; message: string; is_trading_time: boolean}>("/api/trade", {
      method: "POST",
      body: JSON.stringify({ symbol, name, side, order_type: orderType, quantity, price: price || null }),
    }),

  sendFeedback: (agent: string, content: string, feedback: string, conversationId?: string) =>
    request<{ success: boolean; content_hash: string; feedback: string }>("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ agent, content, feedback, conversation_id: conversationId }),
    }),

  getFeedback: () =>
    request<{ data: { content_hash: string; agent: string; feedback: "up" | "down" }[] }>("/api/feedback"),

  settings: {
    get: () => request<{ categories: any[] }>("/api/settings", { method: "POST" }),
    update: (settings: Record<string, string>) => request<{ success: boolean; message: string }>("/api/settings", {
      method: "PUT", body: JSON.stringify({ settings }),
    }),
  },

  scheduler: {
    list: () => request<{ tasks: ScheduledTask[] }>("/api/scheduler/tasks"),
    create: (data: { name: string; agent_key: string; prompt: string; schedule_type: string; interval_seconds?: number; daily_time?: string }) =>
      request<{ success: boolean; task: ScheduledTask }>("/api/scheduler/tasks", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Record<string, unknown>) =>
      request<{ success: boolean }>(`/api/scheduler/tasks/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<{ success: boolean }>(`/api/scheduler/tasks/${id}`, { method: "DELETE" }),
    run: (id: number) =>
      request<{ success: boolean; result: string }>(`/api/scheduler/tasks/${id}/run`, { method: "POST" }),
  },

  arena: {
    status: () => request<ArenaStatus>("/api/arena/status"),
    scorecard: () =>
      request<{ enabled: boolean; scorecard?: ArenaScorecard | null; calibration?: ArenaCalibration | null }>(
        "/api/arena/scorecard"
      ),
    predictions: () =>
      request<{ enabled: boolean; total?: number; predictions?: ArenaPrediction[] }>("/api/arena/predictions"),
    runNow: () =>
      request<{ success: boolean; message?: string; result?: ArenaRunResult }>("/api/arena/run-now", {
        method: "POST",
      }),
  },
};

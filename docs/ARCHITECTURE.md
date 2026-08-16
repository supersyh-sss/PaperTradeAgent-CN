# PaperTradeAgent 系统架构与模块解析

> 定位：面向投资小白的 A 股**模拟交易 + 金融素养教育**多智能体系统（非实盘、非投资建议），
> 同时是一套可作为参考实现的**多智能体（Multi-Agent）应用开发示例**。
> 本文件描述整体架构与各模块职责，架构图见 [architecture.svg](./architecture.svg)。
> 后续优化与扩展建议见 [PLAN.md](./PLAN.md)。

---

## 一、整体架构

系统采用「前端单页应用 + 后端分层服务 + 多智能体编排」的经典三层结构，中间通过 SSE 进行实时通信。

```
User
  │
  ▼
Frontend (React 19 + TypeScript + Tailwind CSS 4)
  │  HTTP / SSE
  ▼
Backend (FastAPI)
  ├── api/          路由层：HTTP 接口与流式响应
  ├── agents/       编排层：LangGraph 多 Agent 协作
  ├── services/     业务层：行情、交易、结算、记忆、RAG、调度等
  ├── harness/      工程框架：可观测、容错、安全、上下文管理
  ├── middleware/   横切关注：鉴权、限流、链路追踪、异常
  └── database/     SQLite schema（账户 / 持仓 / 订单 / 记忆 / trace）
```

**核心链路**：

1. 用户输入进入 `api/chat.py`，经鉴权与中间件后进入 LangGraph 主图（`agents/graph.py`）。
2. `chief_strategist` 做意图识别、任务拆解与 Agent 调度，输出结构化计划 `state["plan"]`。
3. 按需 fan-out 到 4 个执行 Agent（量化研究、市场情报、交易执行、持仓风控）。
4. `quality_gate` 校验输出，失败触发单次有界重试。
5. `response_generator` 汇总生成回复，经 SSE 流式返回前端。

---

## 二、后端模块解析

### 1. `api/` —— 路由层

| 模块 | 职责 |
|------|------|
| `chat.py` | 对话主入口：流式 SSE、交易/自选/撤单等通用待确认动作执行 |
| `market.py` | 行情接口（实时价、K 线、指数） |
| `stocks.py` | 个股查询与详情 |
| `portfolio.py` | 账户资产、持仓、成交历史 |
| `trade.py` | 下单、撤单、委托查询 |
| `watchlist.py` | 自选股增删查 |
| `profile.py` | 用户画像（昵称 / 头像 / 风险偏好，10 题风险问卷） |
| `settings.py` | 系统配置读写（`.env` 热加载） |
| `scheduler.py` | 定时任务 CRUD 与立即执行 |
| `session.py` | 会话管理 |
| `feedback.py` | 消息点赞/点踩反馈 |
| `observability.py` | 运行指标、trace、确定性评估、阈值告警、历史评估、在线 LLM-as-judge |

### 2. `agents/` —— 编排层（LangGraph 多 Agent）

| 模块 | 职责 |
|------|------|
| `graph.py` | LangGraph 状态图：节点编排、条件路由、并行 fan-out/fan-in |
| `state.py` | `AgentState` 状态定义 |
| `schemas.py` | 结构体 / 类型定义 |
| `chief_strategist.py` | 首席策略：意图识别、任务拆解、Agent 调度、计划生成、T+1/账户数据注入 |
| `dispatcher.py` | Agent 调度分发 |
| `quant_researcher.py` | 量化研究员：技术指标、量化评分、目标价预测（多轮工具编排） |
| `market_intelligence.py` | 市场情报：个股消息面、大盘概览、情绪研判（多轮工具编排） |
| `trade_executor.py` | 交易执行：定价、交易计划、止损止盈纪律 |
| `portfolio_monitor.py` | 持仓风控：盈亏、T+1 可卖量、风险提示 |
| `response_generator.py` | 综合分析：报告汇总、普通回复生成 |
| `quality_gate.py` | 质量门：输出校验与 retry 判定 |
| `tool_agent.py` | 多轮 Function Calling 循环（`run_tool_agent`），工具调用次数接入指标收集 |
| `tools.py` | 工具注册表（`TOOLS_BY_AGENT`） |
| `prompts.py` | 各 Agent 系统提示词与画像 |
| `utils.py` | Agent 公共工具函数 |

### 3. `services/` —— 业务层

**行情与数据**

| 模块 | 职责 |
|------|------|
| `data_source_manager.py` | 数据源统一管理（腾讯优先，AKShare 降级） |
| `tencent_api.py` / `sina_api.py` | 第三方行情接口适配 |
| `live_prices.py` | 实时价格轮询与推送 |
| `data_feed.py` | 数据推送/拉取统一入口 |
| `market_tool.py` | 市场工具（实时报价、K 线） |
| `kline_cache.py` | K 线文件缓存 |
| `stock_sync.py` / `stock_lookup.py` | 股票列表同步与查找 |
| `indices.py` | 大盘指数 |
| `symbol.py` | 股票代码标准化（`pure_code`） |

**交易与结算**

| 模块 | 职责 |
|------|------|
| `position_service.py` | 持仓/资金统一结算（`apply_trade_fill`），成本价计入手续费 |
| `order_engine.py` | 订单撮合 |
| `auction_engine.py` | 集合竞价撮合 |
| `trade_rules.py` | 交易规则（T+1、涨跌停、价格笼子） |
| `fee_calculator.py` | A 股费用计算（佣金 / 印花税 / 过户费） |
| `trading_time.py` | 交易日历与交易时段（节假日 + 调休补班） |
| `portfolio_monitor_bg.py` | 后台持仓监控：交易时间持久化巡检 + 分批买卖纪律（个股/组合风控线，SSE 推送 + 会话持久化） |

**分析能力**

| 模块 | 职责 |
|------|------|
| `technical_analysis.py` | 技术指标计算 |
| `quant_signals.py` | 轻量量化指标引擎（ATR/KDJ/CCI/ADX/OBV/MFI/BIAS/PSY 等）与集成评分、市场状态识别 |
| `quant_prediction.py` | 短/中长线目标价预测 |
| `predictions.py` | 预测辅助 |

**记忆与检索**

| 模块 | 职责 |
|------|------|
| `agent_memory.py` | Agent 记忆：两级检索（哈希 + 语义）+ TTL + 命中质量门槛 + 跨会话去重 |
| `rag_service.py` | RAG 语义检索（余弦相似度 + n-gram 降级） |
| `embedding.py` | fastembed 本地 embedding（bge-small-zh-v1.5） |
| `news_service.py` | 新闻获取与持久化（财经源 + 全网搜索聚合） |
| `web_search.py` | DuckDuckGo HTML 全网搜索（无 key 搜索引擎降级） |

**LLM 与评估**

| 模块 | 职责 |
|------|------|
| `llm.py` | LLM 客户端（Flash/Pro 路由 `choose_client`、`chat_with_tools`） |
| `llm_judge.py` | LLM-as-judge 结构化评分 |
| `session_manager.py` | 会话上下文管理（长对话摘要压缩）+ 系统消息队列（`push_system_message` / `poll_messages` / 活跃会话跟踪） |

**基础设施**

| 模块 | 职责 |
|------|------|
| `db.py` | SQLite 异步操作（账户/持仓/订单/记忆/trace/新闻/评估结果） |
| `cache.py` | TTLCache |
| `scheduler.py` | 定时任务后台调度 |
| `task_manager.py` | 后台任务统一管理 |
| `logging_config.py` | 结构化日志 |

### 4. `harness/` —— 工程框架

| 模块 | 职责 |
|------|------|
| `metrics.py` | 指标收集（20+ 指标） |
| `resilience.py` | 熔断、指数退避重试、降级链 |
| `safety_gate.py` | 权限校验（最小权限）、注入检测、审计日志 |
| `sandbox.py` | 工具调用隔离 |
| `context_manager.py` | 上下文优先级与 token 预算 |
| `call_interceptor.py` | 调用拦截 |
| `contracts.py` | 契约定义 |
| `feedback_assembler.py` | 反馈组装 |
| `repl_loop.py` | 循环控制 |
| `state_manager.py` | 状态管理 |

### 5. `middleware/` —— 横切关注

| 模块 | 职责 |
|------|------|
| `error_handler.py` | 统一异常处理 + `get_current_user` 鉴权 |
| `rate_limit.py` | 速率限制 |
| `trace.py` | 链路追踪（`trace_id`） |

### 6. `database/schema.sql`

核心表：`accounts`、`positions`、`trades`、`orders`、`watchlist`、`conversations`、`conversation_messages`、`agent_memory`、`agent_traces`、`market_news`、`user_profiles`、`scheduled_tasks`、`message_feedback`、`locked_state`、`eval_results` 等。

---

## 三、前端模块解析

| 模块 | 职责 |
|------|------|
| `App.tsx` | 应用根：直接渲染聊天主界面 |
| `components/Sidebar.tsx` | 左侧栏：历史会话、快捷操作、自选股、账户资产、动态时间 |
| `components/ChatArea.tsx` | 中间聊天区：消息流、计划视图、报告/交易卡片 |
| `components/StockDetail.tsx` | 右侧个股详情 |
| `components/TradePanel.tsx` / `TradeConfirmPanel.tsx` | 下单与交易确认 |
| `components/ActionConfirmPanel.tsx` | 通用待确认面板（自选/撤单） |
| `components/AddWatchlistDialog.tsx` | 添加自选对话框 |
| `components/SettingsDialog.tsx` | 设置面板（个人资料/风险评估/模型/限流/数据/定时任务/运行统计） |
| `components/Icon.tsx` | 内联 SVG 图标库 |
| `components/ErrorBoundary.tsx` | 前端异常降级 |
| `stores/chatStore.ts` | 全局状态（会话/消息/持仓/自选/行情） |
| `api/client.ts` | HTTP API 封装 |
| `api/sse.ts` | SSE 流式解析 |

---

## 四、测试与评估

| 模块 | 职责 |
|------|------|
| `tests/eval_intent.py` | 意图识别确定性 golden 集（25 条）评测 |
| `tests/test_eval_intent.py` | 上述评测的 pytest 封装（CI 门禁，阈值 0.9） |
| `tests/eval_llm.py` | LLM-as-judge 离线质量评估（不进 CI） |
| `tests/test_rag.py` | RAG 语义检索测试 |
| `tests/test_graph_integration.py` | 图编排集成测试 |
| `tests/test_comprehensive.py` | 综合测试 |
| 其余 `tests/*` | 撮合、数据源、harness、工具函数等单测 |

---

## 五、关键机制

- **费用与成本价**：`fee_calculator` 计算佣金（万 2.5，最低 5 元）、印花税（卖出 0.05%）、过户费（沪市万 0.1）；买入手续费计入持仓成本价（`position_service.apply_trade_fill`）。
- **T+1**：`position_service.compute_sellable` 统一计算当日冻结与可卖数量。
- **记忆**：`agent_memory` 两级检索（L1 哈希精确 + L2 embedding 语义），命中带相似度门槛与长度预算，写入时做跨会话语义去重。
- **可观测**：`agent_traces` 持久化链路 + `/api/observability/overview` 聚合（延迟分位/按意图成功率）+ 阈值告警标记；`eval_results` 持久化评估历史，支持在线 LLM-as-judge。
- **持久化监控与分批买卖纪律**：`portfolio_monitor_bg` 交易时段每 60 秒（非交易时段 300 秒）巡检持仓；按「个股风控线 -8% / 日内波动 ±3% / 组合红线 -5% / 组合回撤 -2%」生成分级建议；`_target_session()` 定位活跃或最近会话，`_emit()` 同时做 SSE 实时推送（`push_system_message`）与会话历史持久化，`_cooldown_ok()` 去重冷却；前端经 `/api/session/stream` 接收并渲染带「按此建议执行」的风控卡片，请示用户是否分批买入/卖出或果断离场。
- **人机协同**：所有写操作走确认面板；低置信度主动提示人工复核；可感知降级。

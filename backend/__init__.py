"""PaperTradeAgent 后端包。

分层结构（自外向内）：

- api/         路由层：FastAPI HTTP 接口与 SSE 流式响应
- agents/      编排层：LangGraph 多 Agent 协作（6 个执行 Agent + 编排节点）
- services/    业务层：行情、交易结算、记忆/RAG、新闻、调度等
- harness/     工程框架：可观测、容错、安全、上下文管理
- middleware/  横切关注：鉴权、限流、链路追踪、异常处理
- database/    SQLite schema（账户/持仓/订单/记忆/trace/新闻等）

详细模块职责见 docs/ARCHITECTURE.md。
"""

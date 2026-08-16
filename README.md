# PaperTradeAgent

<div align="center">

**A multi-agent (AI Agent) A-share paper-trading sandbox for novice investors — six LLM agents coordinated by LangGraph to practice trading operations and build financial literacy under real broker rules, and a ready-to-learn reference implementation for building agentic AI applications.**

[English](README.md) · [简体中文](README.zh-CN.md)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?style=flat&logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1.x-1C3C3C?style=flat&logo=langchain&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?style=flat&logo=react&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-6.0-3178C6?style=flat&logo=typescript&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?style=flat&logo=tailwindcss&logoColor=white)
![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-4D6BFE?style=flat)
![License](https://img.shields.io/badge/license-MIT-green?style=flat)

*Keywords: `multi-agent` · `AI Agent` · `LLM` · `LangGraph` · `paper trading` · `simulated trading` · `A-share` · `quantitative analysis` · `RAG` · `robo-advisor` · `financial education` · `risk control` · `open source` · `full-stack`*

</div>

> **Beta / Test release** — This project is currently in a testing phase and is still being actively improved. We warmly welcome your **usage and feedback** — bug reports, improvement suggestions, or feature ideas are all welcome via [GitHub Issues](https://github.com/supersyh-sss/PaperTradeAgent-CN/issues). Your feedback will directly shape the project's direction.

> **⚠️ Educational simulation only.** PaperTradeAgent is a **paper trading (模拟交易) sandbox** designed to help **novice investors** build confidence, practice trading operations, and grow financial knowledge. It is **NOT a tool for real-world investing** — the market is inherently uncertain, the algorithms are intentionally simplified, and the project does not run on professional quantitative hardware or data infrastructure.

PaperTradeAgent is an open-source full-stack project that simulates A-share trading using six LLM agents coordinated by LangGraph. It covers watchlist, real-time quotes, multi-agent analysis, order placement, matching, position settlement, and risk monitoring, all under real broker rules.

Market quotes and news come entirely from free third-party data sources: Tencent Finance for quotes and K-line, and Eastmoney, CLS, and Sina for news, with a DuckDuckGo web-search fallback for full-web coverage. The project also works as a reference for building multi-agent and agentic-AI systems under hard trading constraints such as T+1, price limits, the trading calendar, commission, and stamp duty.

## Quick Start

```bash
# 1. Python dependencies
uv sync

# 2. Frontend dependencies
cd frontend && npm install && cd ..

# 3. Configure environment
cp .env.example .env      # fill in DEEPSEEK_API_KEY

# 4. Start backend (http://localhost:8001)
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8001

# 5. Start frontend (http://localhost:5173)
cd frontend && npm run dev
```

Core environment variables: `DEEPSEEK_API_KEY` (required), `DEEPSEEK_FLASH_MODEL`, `DEEPSEEK_PRO_MODEL`, `THINKING_MODE` (`auto`/`fast`/`deep`), `INITIAL_BALANCE` (default `1000000.00`).

## Architecture

<p align="center">
  <img src="docs/architecture.svg" alt="PaperTradeAgent system architecture" width="880" />
</p>

## Positioning & Value: Why It's Worth Learning From

PaperTradeAgent is a **simulation- and education-oriented** multi-agent financial terminal. It deliberately **does not connect real funds, give investment advice, or promise returns** — its product goal is "safe practice under real constraints," not misleading anyone into treating simulated results as investment guidance.

It is also an **excellent multi-agent application development reference**. Its "excellence" is grounded in being runnable, testable, evaluable, and re-developable rather than a throwaway demo:

1. **Real business constraints** — T+1, price limits (主板/创业板/科创板/北交所), price collar, commission, stamp duty, and a Chinese trading calendar with holidays + make-up workdays are all encoded in the code, making it a constrained real problem rather than an unconstrained demo.
2. **Explainable task planning** — the Chief Strategist emits a structured `goal / steps / reasoning` plan on every request, backed by a quality gate and bounded reflection, so "why these 6 agents were dispatched" is auditable.
3. **Multi-turn tool calling** — the quant researcher and market-intelligence agent decide what data to fetch via function calling instead of one-shot pre-fetching; tool-call counts feed into observability metrics.
4. **Persistent monitoring + batch buy/sell discipline** — a background loop inspects holdings during trading hours and proactively pops graded suggestions (warning / partial exit / decisive exit) into the active conversation, asking the user before acting. Good buying is staged, good selling is staged, and hitting a risk red line means exiting decisively — the essence of disciplined trading.
5. **Human-in-the-loop with least privilege** — all account-mutating operations go through a confirmation panel; the Safety Gate defaults to read-only, writes require explicit authorization, and low-confidence analysis proactively asks for human review.
6. **Observability & evaluation loop** — agent traces, latency percentiles, per-intent success rates, and online LLM-as-judge are persisted with a frontend panel, and a golden intent dataset gates CI.
7. **Lightweight but professional quant engine** — indicators such as ATR/KDJ/CCI/ADX/OBV/MFI/BIAS/PSY run on CPU without specialized hardware, making it a solid baseline for teaching and secondary development.

## Key Features

- **LLM-first intent understanding** — a Chief Strategist agent independently classifies intent, extracts symbols, decomposes tasks, and routes to the right agents.
- **Six-agent collaboration** — LangGraph orchestrates quant research, market intelligence, trade execution, portfolio risk control, and response synthesis.
- **Position-aware routing** — with holdings, the system proactively monitors portfolio health; without holdings, casual chat stays lightweight.
- **Persistent trading-hours monitoring + batch buy/sell discipline** — a background loop inspects holdings during trading hours and proactively pops graded suggestions into the conversation, asking before staged buys/sells and decisive exit at the risk red line.
- **Broker-grade order engine** — limit/market orders, auction (集合竞价) placement, continuous matching, and realistic fee calculation.
- **A-share market rules** — T+1, price limits (主板/创业板/科创板/北交所), price collar (价格笼子), commission, stamp duty, and a trading calendar with holidays + make-up workdays.
- **Structured task planning** — each request produces a `goal / steps / reasoning` plan, with a quality gate that detects degraded agents and triggers bounded retry.
- **Semantic memory (RAG)** — rolling summary for long conversations plus two-level cache: exact-hash + embedding-based semantic retrieval (fastembed + bge-small-zh-v1.5), with cross-session dedup that refreshes near-duplicate memories instead of piling up duplicates.
- **Lightweight professional quant engine** — extended indicators (ATR/KDJ/ROC/Williams %R/CCI/OBV/ADX + MFI/BIAS/PSY/market-regime) plus a weighted multi-signal ensemble score, runnable on CPU without specialized hardware.
- **Dual-horizon forecasting** — short-term (3–5 minute) order-price targets balanced between fill probability and entry slippage; medium/long-term (day/month/year) targets via trend + support/resistance + ATR.
- **Disciplined strategy synthesis** — fundamentals (PE/PB/turnover) + technicals + market sentiment are fused into trading plans with ATR-based stop-loss/take-profit discipline.
- **Dual-model routing + thinking mode** — DeepSeek Flash for dialogue/intelligence, DeepSeek Pro for technical analysis / pricing / risk; a `THINKING_MODE` switch (`auto`/`fast`/`deep`) routes per-scenario to trade off speed vs. depth.
- **User profile in Settings** — nickname, avatar, initial balance, and a 10-question deterministic risk questionnaire are all managed in Settings → 个人资料; new users start with a default profile (¥1,000,000 balance, a default avatar/nickname, and a balanced risk level).
- **Agent scheduling** — interval/daily scheduled tasks are created from the Settings panel and executed by a background scheduler loop that reuses the LangGraph multi-agent pipeline.
- **SSE streaming + real-time quotes** — reasoning renders token-by-token; quotes batch-poll every second and push via `EventSource`.
- **News with source attribution** — market intelligence output includes source labels and original links, aggregated from financial sources plus a DuckDuckGo web-search fallback for full-web coverage.
- **Human-in-the-loop & least-privilege** — account-mutating operations require explicit confirmation; Safety Gate defaults to read-only, writes need explicit authorization; graceful degradation is always labeled, and low-confidence analysis proactively asks for human review.
- **Observability & evaluation** — `agent_traces` persist per-agent latency/status/token usage; `eval_results` persist deterministic and LLM-judge runs; latency percentiles and per-intent success rates aggregate in the overview; an on-demand LLM-as-judge endpoint scores intent/quality online; a golden intent dataset gates CI; and a Settings → 运行统计 panel exposes metrics/traces/evaluation/audit in the UI.

## Agent Team

| Agent | Responsibility | Model |
|-------|----------------|-------|
| **Chief Strategist** | intent recognition, task decomposition, agent dispatch, position awareness | Flash |
| **Quant Researcher** | technical analysis (MA/RSI/MACD/Bollinger + ATR/KDJ/ROC/CCI/OBV/ADX/MFI/BIAS/PSY), multi-signal score, market regime, fundamentals | Pro |
| **Market Intelligence** | real-time news (东方财富/财联社/新浪 + DuckDuckGo web search), sentiment, source attribution | Flash |
| **Trade Executor** | dual-horizon pricing (3–5 min / day-month-year), fee estimation, ATR discipline, order execution | Pro |
| **Portfolio Monitor** | P&L, concentration risk, drawdown monitoring, auction gap alerts | Pro |
| **Response Generator** | synthesizes agent outputs into a final report | Flash |

Directly address an agent with `@量化`/`@quant`, `@市场`/`@情报`/`@intel`, `@交易`/`@trade`, `@风控`/`@持仓`/`@portfolio`, or `@助手`. Multiple agents can be mentioned at once.

## Harness Engineering

The LLM agents run inside a harness that turns non-deterministic model output into a deterministic engineering flow.

| Mechanism | What it does |
|-----------|--------------|
| REPL Loop | Read (perception) → Eval (execution) → Print (feedback) → Loop (control) across the agent lifecycle |
| Context Pipeline | information aggregation → relevance ranking → summarization → budget allocation → template assembly |
| Call Interceptor | schema serialization, deterministic deserialization, observability injection, fallback chain |
| Safety Gate | least-privilege permission checks (read-only by default, write operations require explicit authorization), sensitive-data filtering, injection defense, audit logging |
| Resilience | circuit breaker + retry with exponential backoff and jitter |
| Sandbox & Contracts | isolation levels and per-agent contracts; state checkpoints for rollback |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + LangGraph + SSE |
| LLM | DeepSeek API (flash / pro models) |
| Data | Tencent Finance API; 东方财富 / 财联社 / 新浪 (news) + DuckDuckGo web search |
| Database | SQLite (aiosqlite, WAL) |
| Cache | TTLCache + JSON files (K-line) + in-memory poller (quotes) |
| Frontend | React 19 + TypeScript + Tailwind CSS 4 + Vite |
| Charts | ECharts (K-line + MA + B/S + volume) |
| State | Zustand |
| QA | pytest + pytest-asyncio + ruff + GitHub Actions CI |

## Trading Rules

| Rule | Detail |
|------|--------|
| Auction | 9:15–9:20 cancelable / 9:20–9:25 not cancelable / 9:25 match |
| Continuous | 9:30–11:30, 13:00–15:00 (closed weekends/holidays) |
| T+1 | Shares bought today are sellable tomorrow |
| Price limit | 主板 ±10%, 创业板/科创板 ±20%, 北交所 ±30% |
| Price collar | buy ≤ 102% of reference, sell ≥ 98% of reference |
| Commission | 0.025% (min ¥5), both sides |
| Stamp duty | 0.05%, sell only |

## Suggestions & Planning

See [docs/PLAN.md](docs/PLAN.md) for optimization and extension suggestions grounded in the current frontend/backend modules, including:

1. **Scheduler extension** — cron expressions, trading-calendar-aware triggers, writing scheduled results back to the conversation, and reusable task templates.
2. **Persistent monitoring optimization** — richer monitoring dimensions, quantified batch plans, a confirm-then-execute loop, and backtest-style review.
3. **Complex buy/sell strategies** — strategy templates, layered trade plans, state-machine-driven staged execution, and scheduler coordination.
4. **Engineering finish-up** — containerization, database migration, and API versioning/pagination.

## Disclaimer

- This project is a **simulated trading (paper trading) educational system**. All account balances, orders, and P&L are virtual.
- It does **not** provide investment advice and is **not** intended for real-money trading.
- Market data and news come from public third-party sources and may be delayed or inaccurate.
- The analysis is generated by LLMs and may contain errors; it is for learning purposes only.

## Contributing

Contributions are welcome. Please keep the project's educational positioning in mind, and prefer small, focused changes. The codebase follows a layered backend (`api` → `agents` → `services` → `harness`) plus a typed React frontend.

## License

[MIT](LICENSE) © 2026 supersyh

## Acknowledgments

- [LangGraph](https://github.com/langchain-ai/langgraph) — multi-agent orchestration
- [DeepSeek](https://www.deepseek.com/) — LLM provider
- [Tencent Finance API](https://qt.gtimg.cn/) / [东方财富](https://www.eastmoney.com/) / [财联社](https://www.cls.cn/) / [新浪财经](https://finance.sina.com.cn/) — market data & news
- [AKShare](https://github.com/akfamily/akshare) — financial data fallback

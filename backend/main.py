"""FastAPI 主应用入口"""
import logging
import os
import sys
from contextlib import asynccontextmanager

# 将backend目录添加到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.chat import router as chat_router
from .api.feedback import router as feedback_router
from .api.market import router as market_router
from .api.observability import router as observability_router
from .api.portfolio import router as portfolio_router
from .api.profile import router as profile_router
from .api.scheduler import router as scheduler_router
from .api.session import router as session_router
from .api.settings import router as settings_router
from .api.stocks import router as stocks_router
from .api.trade import router as trade_router
from .api.watchlist import router as watchlist_router
from .config import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGINS
from .middleware.error_handler import global_exception_handler
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.trace import TraceMiddleware
from .services.db import init_db
from .services.live_prices import (
    _on_order_status_change,
    set_fill_callback,
    start_live_service,
)
from .services.logging_config import configure_root_logger
from .services.order_engine import register_status_callback, restore_orders
from .services.task_manager import task_manager
from .services.trading_time import TradingTimeChecker

configure_root_logger(level=logging.INFO, json_format=True)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    await init_db()
    logger.info("数据库初始化完成")
    logger.info(f"交易状态: {TradingTimeChecker.trading_status_info()}")
    
    # Harness 初始化
    try:
        from .harness.metrics import MetricsCollector
        from .harness.safety_gate import AuditLogger
        MetricsCollector().start_session("server_boot")
        AuditLogger.log("server_start", {"version": "0.1.0"})
        logger.info("Harness 工程框架已初始化")
    except Exception as e:
        logger.warning(f"Harness 初始化跳过: {e}")
    
    # 恢复未完成的订单
    restored = await restore_orders()
    if restored:
        logger.info(f"已恢复 {restored} 个未完成订单")
    
    # 注册订单成交回调（自动更新持仓和余额）
    set_fill_callback(_on_order_filled)

    # 注册订单状态变更回调（SSE 推送）
    register_status_callback(_on_order_status_change)

    # 启动实时行情轮询服务
    start_live_service()
    logger.info("实时行情轮询服务已启动")

    # 启动 TaskManager
    await task_manager.startup()

    # 启动后台持仓监控任务
    from .services.portfolio_monitor_bg import start_portfolio_monitor
    task_manager.create_task(start_portfolio_monitor(), name="portfolio_monitor")
    logger.info("后台持仓监控任务已启动")

    # 启动定时任务调度循环
    from .services.scheduler import start_scheduler
    start_scheduler()
    logger.info("定时任务调度循环已启动")

    yield
    # 关闭时清理
    await task_manager.shutdown()


async def _on_order_filled(fill: dict):
    """订单成交回调：统一使用 position_service 完成资金和持仓结算"""
    try:
        from .services.fee_calculator import calculate_fee
        from .services.position_service import apply_trade_fill
        from .services.symbol import exchange_prefix
        user_id = fill.get("user_id", "default")
        symbol = fill.get("symbol", "")
        name = fill.get("name", "")
        side = fill.get("side")
        qty = fill.get("quantity", 0)
        price = fill.get("price", 0)
        amount = fill.get("amount", 0)

        if not symbol or not side or qty <= 0:
            logger.warning(f"成交回调参数无效: {fill}")
            return

        fee, _ = calculate_fee(amount, side, exchange_prefix(symbol))

        result = await apply_trade_fill(
            user_id=user_id,
            symbol=symbol,
            name=name,
            side=side,
            quantity=qty,
            price=price,
            amount=amount,
            order_type=fill.get("order_type", "MARKET"),
            order_id=fill.get("order_id"),
            lock_price=fill.get("lock_price", price),
            fee=fee,
        )
        logger.info(
            f"成交回调: {result['message']} 余额{result['new_balance']:.2f} "
            f"总资产{result['total_assets']:.2f}"
        )
    except Exception:
        logger.exception("成交回调异常")


app = FastAPI(
    title="PaperTradeAgent - A股模拟投资交易系统",
    description="多Agent协作的A股模拟投资系统，基于LangGraph编排",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS：带凭证时不能允许所有来源，改从配置读取可信域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)

# Trace ID 全链路追踪（需在 CORS 之后、其他中间件之前）
app.add_middleware(TraceMiddleware)

# 本地单用户部署：宽松限流（2000 次/分钟），仅用于兜底防止失控循环
app.add_middleware(RateLimitMiddleware, max_requests=2000, window_seconds=60)

# 注册全局异常处理
app.add_exception_handler(Exception, global_exception_handler)

# 注册路由
app.include_router(chat_router)
app.include_router(trade_router)
app.include_router(portfolio_router)
app.include_router(watchlist_router)
app.include_router(stocks_router)
app.include_router(market_router)
app.include_router(settings_router, prefix="/api/settings")
app.include_router(session_router)
app.include_router(feedback_router)
app.include_router(observability_router)
app.include_router(profile_router)
app.include_router(scheduler_router)


@app.get("/api/health")
async def health_check():
    """健康检查 + 交易状态"""
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "trading": TradingTimeChecker.trading_status_info(),
    }


@app.get("/api/harness/status")
async def harness_status():
    """Harness 工程框架状态诊断"""
    try:
        from .harness.contracts import ContractRegistry
        from .harness.metrics import MetricsCollector
        from .harness.safety_gate import AuditLogger
        from .harness.sandbox import SandboxManager
        
        metrics = MetricsCollector().get_summary()
        audit_recent = AuditLogger.get_recent(20)
        sandbox = SandboxManager().get_sandbox_info()
        contracts = {k: c.description for k, c in ContractRegistry()._contracts.items()}
        
        return {
            "framework": "Harness v1.0",
            "principles": [
                "Design for Failure",
                "Contract-First",
                "Secure by Default",
                "Separation of Concerns",
                "Everything is Measurable",
                "Data-driven Evolution",
            ],
            "core_mechanisms": {
                "context_pipeline": "聚合→排序→压缩→预算→模板",
                "call_lifecycle": "Schema序列化→触发生成→确定性反序列化→观测注入",
                "fallback_chain": "JSON→Regex→LineParse",
                "isolation_levels": sandbox["risk_gates"],
            },
            "metrics_summary": metrics,
            "recent_audit": len(audit_recent),
            "registered_contracts": contracts,
        }
    except Exception as e:
        return {"error": str(e), "status": "harness_partial"}


@app.get("/api/harness/metrics")
async def harness_metrics_detail():
    """Harness 详细度量指标（基于持久化 agent_traces 聚合，L5 可观测性）"""
    try:
        from .services.db import get_metrics_summary
        persisted = await get_metrics_summary()
        # session_metrics 与 agent_traces 共用同一份持久化数据，避免空壳
        session_metrics = {
            "sessions": persisted.get("total_sessions", 0),
            "avg_success_rate": persisted.get("avg_success_rate", 0),
            "total_llm_errors": persisted.get("total_failed", 0),
            "total_tokens": persisted.get("total_tokens", 0),
        }
        return {
            "session_metrics": session_metrics,
            "agent_traces": persisted,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/")
async def root():
    return {"message": "PaperTradeAgent API is running", "version": "0.1.0"}

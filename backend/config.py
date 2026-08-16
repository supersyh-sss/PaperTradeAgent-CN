"""环境变量配置 - 所有配置从 .env 读取，无硬编码敏感信息"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 加载 .env 文件
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    _env_path = _PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    logger.debug("dotenv not installed")

def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default

def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except (TypeError, ValueError):
        return default

# ── DeepSeek LLM API ──
DEEPSEEK_API_KEY = _env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# Flash: 快速轻量模型，用于简单任务（对话/分类/文本结构化）
DEEPSEEK_FLASH_MODEL = _env("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
# Pro: 深度推理模型，用于复杂任务（技术分析/交易计划/股价预估/风险研判）
DEEPSEEK_PRO_MODEL = _env("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro")
# 思考模式：auto=按场景自动路由 | fast=全部走 Flash（关闭深度思考，最快）| deep=深度分析强制 Pro
THINKING_MODE = _env("THINKING_MODE", "auto")

# ── Database ──
_default_db = str(_PROJECT_ROOT / "backend" / "database" / "paper_trade.db")
DB_PATH = os.getenv("DB_PATH", _default_db)

# ── Backend Server ──
BACKEND_HOST = _env("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = _env_int("BACKEND_PORT", 8001)

# ── Frontend Server ──
FRONTEND_PORT = _env_int("FRONTEND_PORT", 5173)

# Auth (MVP)
TEST_TOKEN = _env("TEST_TOKEN", "Bearer mvp_test_token_2026")
TEST_USER_ID = _env("TEST_USER_ID", "default")

# CORS
# 生产环境应配置具体域名，开发环境默认允许前端 localhost
def _parse_cors_origins(raw: str):
    if not raw:
        return ["http://localhost:5173", "http://127.0.0.1:5173"]
    return [o.strip() for o in raw.split(",") if o.strip()]

CORS_ALLOW_ORIGINS = _parse_cors_origins(_env("CORS_ALLOW_ORIGINS", ""))
CORS_ALLOW_CREDENTIALS = _env("CORS_ALLOW_CREDENTIALS", "true").lower() in ("1", "true", "yes")

# ── Trading Simulation ──
INITIAL_BALANCE = _env_float("INITIAL_BALANCE", 1000000.00)
WATCHLIST_MAX_SIZE = _env_int("WATCHLIST_MAX_SIZE", 5)

# Polling & Cache
# 实时行情轮询（基于腾讯财经API限流测试优化）
# 测试结果：200+代码/次，8 req/s 持续15s 零拒绝，10并发零拒绝
# 策略：单次批量拉取全部符号，1s间隔 = 1 req/s，极充裕余量
LIVE_PRICE_POLL_INTERVAL = _env_int("LIVE_PRICE_POLL_INTERVAL", 1)  # 秒
PRICE_CACHE_TTL = _env_int("PRICE_CACHE_TTL", 5)  # 秒（已废弃，实时轮询替代）
KLINE_CACHE_TTL = _env_int("KLINE_CACHE_TTL", 3600)  # 秒

# API限流（腾讯财经实测宽松，保守设置远超实际需求）
# 10 req/s 为安全上限（实测 8 req/s 零拒绝，加 25% 余量）
API_RATE_LIMIT = _env_int("API_RATE_LIMIT", 10)  # 次
API_RATE_WINDOW = _env_int("API_RATE_WINDOW", 1)  # 秒

# ── HTTP Timeouts ──
HTTP_TIMEOUT_STOCK = _env_int("HTTP_TIMEOUT_STOCK", 10)
HTTP_TIMEOUT_LLM_CHAT = _env_int("HTTP_TIMEOUT_LLM_CHAT", 60)
HTTP_TIMEOUT_LLM_STREAM = _env_int("HTTP_TIMEOUT_LLM_STREAM", 120)

# Conversation Memory
MAX_RECENT_MESSAGES = _env_int("MAX_RECENT_MESSAGES", 20)
MAX_TOTAL_MESSAGES = _env_int("MAX_TOTAL_MESSAGES", 40)
SUMMARY_TRIM_THRESHOLD = _env_int("SUMMARY_TRIM_THRESHOLD", 30)

# ── RAG / Embedding ──
# 语义检索嵌入模型（fastembed 支持，ONNX 本地推理，无需 torch）
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_DEVICE = _env("EMBEDDING_DEVICE", "cpu")
# 语义检索相似度阈值（余弦相似度，0~1，越高越严格、越少误命中）
EMBEDDING_SIMILARITY_THRESHOLD = _env_float("EMBEDDING_SIMILARITY_THRESHOLD", 0.55)
# Agent 记忆语义命中质量门槛：命中分数低于该值则放弃缓存、触发实时 LLM（略高于检索阈值）
AGENT_MEMORY_MIN_SCORE = _env_float("AGENT_MEMORY_MIN_SCORE", 0.6)
# Agent 记忆命中结果长度预算（字符）：超长结果放弃复用（JSON）或截断（纯文本）
AGENT_MEMORY_MAX_RESULT_CHARS = _env_int("AGENT_MEMORY_MAX_RESULT_CHARS", 4000)
# Agent 记忆跨会话去重阈值：新 query 与既有记忆余弦相似度达到该值即视为重复，刷新而非新增
AGENT_MEMORY_DEDUP_SCORE = _env_float("AGENT_MEMORY_DEDUP_SCORE", 0.92)

# ── Harness Engineering Framework ──
# Token预算：分配给 LLM 上下文窗口的最大 Token 数
HARNESS_TOKEN_BUDGET = _env_int("HARNESS_TOKEN_BUDGET", 4000)
# 重试策略：最大重试次数
HARNESS_MAX_RETRIES = _env_int("HARNESS_MAX_RETRIES", 3)
# 熔断器：连续失败阈值
HARNESS_CIRCUIT_BREAKER_THRESHOLD = _env_int("HARNESS_CIRCUIT_BREAKER_THRESHOLD", 5)
# 熔断器：恢复超时（毫秒）
HARNESS_RECOVERY_TIMEOUT_MS = _env_int("HARNESS_RECOVERY_TIMEOUT_MS", 30000)
# 上下文压缩：触发摘要的最小消息数
HARNESS_CONTEXT_COMPRESSION_THRESHOLD = _env_int("HARNESS_CONTEXT_COMPRESSION_THRESHOLD", 20)
# 沙箱：当前隔离等级 (1=进程, 2=容器, 3=MicroVM, 4=完整VM)
HARNESS_ISOLATION_LEVEL = _env_int("HARNESS_ISOLATION_LEVEL", 1)
# 审计日志：最大保留条数
HARNESS_AUDIT_MAX_ENTRIES = _env_int("HARNESS_AUDIT_MAX_ENTRIES", 10000)
# 度量：历史会话最大保留数
HARNESS_METRICS_MAX_SESSIONS = _env_int("HARNESS_METRICS_MAX_SESSIONS", 1000)
# 安全：是否启用输入净化
HARNESS_INPUT_SANITIZATION = _env("HARNESS_INPUT_SANITIZATION", "true").lower() in ("1", "true", "yes")

# Finance API URLs
TENCENT_REALTIME_URL = "http://qt.gtimg.cn/q={codes}"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_REALTIME_URL = "http://hq.sinajs.cn/list={codes}"

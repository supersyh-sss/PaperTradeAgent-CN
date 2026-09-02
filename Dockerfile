# syntax=docker/dockerfile:1
# ============================================================
# PaperTradeAgent 后端镜像（uv 管理的 Python 3.11 运行环境）
# 构建：docker build -t papertrade-backend .
# ============================================================
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=0

WORKDIR /app

# 1) 先装 uv（体积小、缓存友好）
RUN pip install --no-cache-dir "uv>=0.5"

# 2) 仅拷贝依赖清单 → 先解析依赖，最大化利用构建缓存层
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 3) 拷贝业务代码与内置数据
COPY backend ./backend
COPY data ./data

# 4) 运行时配置默认值（敏感项由 docker-compose / 环境变量注入）
COPY .env.example .env

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8001/api/health', timeout=3)"]

CMD [".venv/bin/uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]

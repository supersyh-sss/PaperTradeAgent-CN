# PaperTradeAgent 常用开发命令入口
# 用法：make test / make lint / make dev 等（Windows 可用 git-bash 或 WSL）

.PHONY: install test lint format check precommit-install dev backend frontend \
        build-fe docker-up docker-down clean

## 安装后端依赖（uv 自动创建 .venv）
install:
	uv sync

## 运行后端测试
test:
	uv run pytest -q

## 静态检查（lint）
lint:
	uv run ruff check .

## 统一代码格式
format:
	uv run ruff format .

## 本地完整检查（= CI 后端 job 同款）
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pytest -q

## 安装 pre-commit 钩子
precommit-install:
	uv run pre-commit install

## 启动后端开发服务（热重载）
backend:
	uv run uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

## 启动前端开发服务
frontend:
	cd frontend && npm run dev

## 前端 lint + 类型检查 + 构建（= CI 前端 job 同款）
build-fe:
	cd frontend && npm run lint && npm run build

## Docker 一键启动（前端 5173 / 后端 8001）
docker-up:
	docker compose up --build

docker-down:
	docker compose down

## 清理 Python 缓存
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache

"""API 路由层。

对外暴露 FastAPI 路由，按业务域拆分：

- chat / trade / portfolio / watchlist / stocks / market —— 对话与交易主链路
- settings / session / feedback / observability / profile / scheduler —— 配置与支撑

各模块以 `router = APIRouter(...)` 定义路由，统一在 `backend/main.py` 注册。
"""

"""横切关注中间件。

- error_handler：统一异常处理 + `get_current_user` 鉴权
- rate_limit：速率限制
- trace：链路追踪（`trace_id`）

中间件在 `backend/main.py` 中按序注册（CORS → Trace → RateLimit）。
"""

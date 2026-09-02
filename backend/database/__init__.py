"""database：SQLite 数据层。

- schema.sql   基线 DDL（全部 CREATE ... IF NOT EXISTS，幂等，随启动执行）
- migrations.py 版本化迁移（schema_migrations 记录已应用版本，只追加不修改）
- paper_trade.db 运行时数据文件（gitignore，由 backend/services/db.py::init_db 自动创建）

详细说明见 docs/ARCHITECTURE.md 与 migrations.py 顶部文档。
"""

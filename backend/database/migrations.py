"""版本化数据库迁移（SQLite / aiosqlite）。

为什么需要：
- schema.sql 只表达“最新状态”，无法回答“某个旧库从哪一版升上来、要补哪些变更”；
- 迁移把每一次结构变更固化为一个不可变的“版本”，任何环境（新克隆的库、已运行
  数周的开发库、CI 临时库）都能收敛到同一 schema 状态。

使用约束（生产级规范）：
1. **只追加**：新变更 = 在 MIGRATIONS 末尾追加更高 version；禁止编辑/删除已发布版本，
   否则会与已记录 applied 版本冲突或导致旧库升级错乱；
2. **原子性**：每个版本与其“已应用”记录写入同一个 BEGIN IMMEDIATE 事务。中途失败自动
   ROLLBACK，绝不会出现“DDL 已生效但版本未记录”的中间态；
3. **幂等**：重复执行只会应用缺失版本；已应用版本直接跳过；
4. **对既有库友好**：历史演进中的 ALTER TABLE 通过 PRAGMA 反射按缺失列补齐（幂等），
   不依赖 try/except 吞异常——异常意味着真实失败，应当让它显式抛出。

首次引入该机制时（v1）把此前散落在 services/db.py 中的“运行时自动迁移”全部收编：
market_news / eval_results 建表、历史遗留列升级。schema.sql 已内建的对象（accounts、
trades、positions、agent_memory、agent_traces 及索引）属于基线，不在此重复。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    """一个不可变迁移版本。

    version:   单调递增，作为主键记录在 schema_migrations；
    name:      人类可读的变更说明；
    statements: 纯 SQL（逐条执行，适用 CREATE TABLE/INDEX 等幂等 DDL）；
    apply:      异步函数 (db) -> None，适用于需要 PRAGMA 反射等运行时判断的升级。
    两者至少提供其一。
    """

    version: int
    name: str
    statements: tuple[str, ...] = ()
    apply: Callable[["object"], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if not self.statements and self.apply is None:
            raise ValueError(f"migration v{self.version} 必须提供 statements 或 apply")


async def _table_columns(db, table: str) -> set[str]:
    """返回表当前存在的列名集合（通过 PRAGMA 反射，连接无需 row_factory）。"""
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    cols: set[str] = set()
    for row in rows:
        # PRAGMA 返回 (cid, name, type, notnull, dflt_value, pk)
        cols.add(row[1] if not isinstance(row, dict) else row["name"])
    return cols


async def _apply_legacy_column_upgrades(db) -> None:
    """历史库升级：补齐旧 schema 缺失、新 schema 已内建的列。

    早期版本通过“逐条 ALTER + try/except 吞异常”打补丁升级旧库；此处改为
    PRAGMA 反射后按缺失列补齐——幂等且任何异常都是真实错误（应显式暴露）。
    """
    legacy_columns: dict[str, list[tuple[str, str]]] = {
        "trades": [
            ("order_id", "TEXT"),
            ("filled_qty", "INTEGER DEFAULT 0"),
            ("filled_amount", "DECIMAL(15,2) DEFAULT 0"),
            ("fill_price", "DECIMAL(10,3)"),
            ("lock_price", "DECIMAL(10,3)"),
            ("lock_fee", "DECIMAL(10,2) DEFAULT 0"),
            ("cancel_reason", "TEXT"),
            ("realized_pnl", "DECIMAL(15,2)"),
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ],
        "positions": [
            ("t1_quantity", "INTEGER DEFAULT 0"),
            ("t1_date", "DATE"),
        ],
        "agent_memory": [
            ("query", "TEXT"),
            ("embedding", "TEXT"),
        ],
    }
    for table, columns in legacy_columns.items():
        existing = await _table_columns(db, table)
        for column, ddl in columns:
            if column not in existing:
                logger.info("迁移: 为 %s 补列 %s", table, column)
                # 表名/列名为模块内常量，不构成注入面
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# 不可变迁移历史（只追加）。v1 对应“迁移机制首次引入”时的 schema 状态。
MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        name="基线收编：market_news / eval_results 建表 + 历史遗留列升级",
        statements=(
            # 新闻持久化（原 services/db.py 内联迁移）
            """CREATE TABLE IF NOT EXISTS market_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source TEXT,
                url TEXT,
                content TEXT,
                symbols TEXT,
                sentiment_score REAL DEFAULT 0,
                crawled_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(title, source)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_market_news_crawled ON market_news(crawled_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_market_news_symbols ON market_news(symbols)",
            # 评估结果持久化（确定性 eval + LLM-as-judge）
            """CREATE TABLE IF NOT EXISTS eval_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eval_type TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'deterministic',
                total INTEGER DEFAULT 0,
                correct INTEGER DEFAULT 0,
                accuracy REAL DEFAULT 0,
                score REAL DEFAULT 0,
                detail TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            "CREATE INDEX IF NOT EXISTS idx_eval_results_type ON eval_results(eval_type, created_at DESC)",
        ),
        apply=_apply_legacy_column_upgrades,
    ),
]

# 预期最新版本（文档/巡检用）
LATEST_MIGRATION_VERSION: int = max(m.version for m in MIGRATIONS)


async def run_migrations(db) -> list[str]:
    """将未应用的迁移按版本升序执行；返回本次实际应用的版本描述列表。

    约定：调用方已先执行 schema.sql 基线（全部幂等 DDL），
    因此本函数只负责“基线之上”的增量变更。
    """
    await db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await db.commit()

    cursor = await db.execute("SELECT version FROM schema_migrations")
    rows = await cursor.fetchall()
    applied: set[int] = {
        (row["version"] if isinstance(row, dict) else row[0]) for row in rows
    }

    applied_now: list[str] = []
    for mig in sorted(MIGRATIONS, key=lambda m: m.version):
        if mig.version in applied:
            continue
        try:
            await db.execute("BEGIN IMMEDIATE")
            for sql in mig.statements:
                await db.execute(sql)
            if mig.apply is not None:
                await mig.apply(db)
            await db.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (mig.version, mig.name),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.error("数据库迁移失败 v%d %s，已整体回滚", mig.version, mig.name)
            raise
        applied_now.append(f"v{mig.version} {mig.name}")
        logger.info("数据库迁移已应用: v%d %s", mig.version, mig.name)
    return applied_now

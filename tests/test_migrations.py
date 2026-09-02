"""数据库版本化迁移的回归测试。

覆盖三件事（对应 backend/database/migrations.py 的设计约束）：
1. 全新库：init_db 后 schema_migrations 只记录一次 v1，重复 init 幂等；
2. 旧库升级：缺列/缺对象会被补齐，且升级结果与全新库一致（收敛性）；
3. 事务原子性：迁移中途失败时，已执行的 DDL 与版本记录一同回滚。
"""

import sqlite3

import aiosqlite
import pytest

from backend.database.migrations import LATEST_MIGRATION_VERSION, run_migrations
from backend.services import db as db_mod


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    """与其余测试一致：把 DB_PATH 指向独立临时库后执行完整初始化。"""
    target = tmp_path / "test_migrations.db"
    monkeypatch.setattr(db_mod, "DB_PATH", str(target))
    await db_mod.init_db()
    async with aiosqlite.connect(str(target)) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn


async def _fetch_version(db) -> list[int]:
    cursor = await db.execute("SELECT version FROM schema_migrations ORDER BY version")
    rows = await cursor.fetchall()
    return [row[0] if not isinstance(row, dict) else row["version"] for row in rows]


async def test_fresh_init_records_all_migrations_once(db):
    """全新库：v1 被应用且只记录一次；重复 init_db 幂等。"""
    versions = await _fetch_version(db)
    assert versions == list(range(1, LATEST_MIGRATION_VERSION + 1))

    # 重复初始化（服务重启场景）不得重复应用、不得报错
    await db_mod.init_db()
    versions = await _fetch_version(db)
    assert versions == list(range(1, LATEST_MIGRATION_VERSION + 1))


async def test_fresh_db_has_migrated_objects(db):
    """全新库可直接查询迁移收编的对象（market_news / eval_results）。"""
    for table in ("market_news", "eval_results", "schema_migrations"):
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        assert await cursor.fetchone() is not None, f"缺少迁移对象 {table}"


async def test_legacy_db_upgrade_converges(tmp_path):
    """旧库升级：只跑 schema 基线 → 通过迁移补齐对象/列，结果与全新库一致。"""
    legacy = tmp_path / "legacy.db"
    conn = await aiosqlite.connect(str(legacy))
    # 早期版本库：仅有核心表，且缺新 schema 已内建的列
    await conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            symbol TEXT NOT NULL, name TEXT NOT NULL,
            side TEXT NOT NULL, order_type TEXT NOT NULL,
            quantity INTEGER NOT NULL, price DECIMAL(10,3) NOT NULL,
            amount DECIMAL(15,2) NOT NULL,
            status TEXT NOT NULL DEFAULT 'FILLED'
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            symbol TEXT NOT NULL, name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            avg_cost DECIMAL(10,3) NOT NULL,
            total_cost DECIMAL(15,2) NOT NULL,
            buy_date DATE NOT NULL
        );
        CREATE TABLE agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_key TEXT NOT NULL, query_hash TEXT NOT NULL,
            result TEXT NOT NULL, expires_at TIMESTAMP NOT NULL
        );
        """
    )
    await conn.commit()
    await conn.close()

    # 只执行“基线之上的增量迁移”（模拟旧库走升级路径）
    conn = await aiosqlite.connect(str(legacy))
    applied = await run_migrations(conn)
    await conn.close()
    assert any("v1" in item for item in applied)

    # 升级后：补齐的列 + 收编的对象全部存在
    conn = await aiosqlite.connect(str(legacy))
    conn.row_factory = aiosqlite.Row
    try:
        for table, expected_col in {
            "trades": ("order_id", "filled_qty", "created_at", "updated_at"),
            "positions": ("t1_quantity", "t1_date"),
            "agent_memory": ("query", "embedding"),
        }.items():
            cursor = await conn.execute(f"PRAGMA table_info({table})")
            cols = {row["name"] for row in await cursor.fetchall()}
            for col in expected_col:
                assert col in cols, f"{table} 缺少升级列 {col}"
        for table in ("market_news", "eval_results", "schema_migrations"):
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            assert await cursor.fetchone() is not None

        # 幂等：再跑一次不产生新变更
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM schema_migrations")
        before = dict(await cursor.fetchone())["n"]
        await run_migrations(conn)
        cursor = await conn.execute("SELECT COUNT(*) AS n FROM schema_migrations")
        assert dict(await cursor.fetchone())["n"] == before
    finally:
        await conn.close()


async def test_failed_migration_rolls_back_atomically(tmp_path, monkeypatch):
    """迁移失败时整体回滚：无半成品 DDL、无版本记录。"""
    target = tmp_path / "atomic.db"
    conn = await aiosqlite.connect(str(target))
    # 先建 schema_migrations，让迁移器认为“已就绪”
    await conn.execute(
        """CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await conn.commit()
    await conn.close()

    # 注入一个必然失败的迁移（前一条 DDL 成功、后一条失败），验证原子回滚。
    # 只保留该失败迁移（隔离 v1），使断言不受其它版本副作用干扰。
    class _Bad:
        version = 999
        name = "注定失败的迁移"
        statements = (
            "CREATE TABLE market_news (id INTEGER PRIMARY KEY);",
            "SELECT * FROM no_such_table",
        )
        apply = None

    from backend.database import migrations as mig_mod

    monkeypatch.setattr(mig_mod, "MIGRATIONS", [_Bad()])
    conn = await aiosqlite.connect(str(target))
    with pytest.raises(sqlite3.OperationalError):
        await mig_mod.run_migrations(conn)
    await conn.close()

    # 失败迁移的 DDL 与记录都应回滚：v999 未记录、其半成品对象不存在
    conn = await aiosqlite.connect(str(target))
    conn.row_factory = aiosqlite.Row
    try:
        # 半途失败的整体回滚：v999 不应留下任何版本记录
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=999"
        )
        assert dict(await cursor.fetchone())["n"] == 0
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_news'"
        )
        assert await cursor.fetchone() is None
    finally:
        await conn.close()

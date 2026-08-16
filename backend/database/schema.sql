-- 用户账户表
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default' UNIQUE,
    balance DECIMAL(15,2) NOT NULL DEFAULT 1000000.00,
    frozen_amount DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    total_assets DECIMAL(15,2) NOT NULL DEFAULT 1000000.00,
    watchlist_max INTEGER NOT NULL DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 自选股表
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol)
);

-- 持仓表
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    avg_cost DECIMAL(10,3) NOT NULL,
    total_cost DECIMAL(15,2) NOT NULL,
    buy_date DATE NOT NULL,
    t1_quantity INTEGER DEFAULT 0,
    t1_date DATE,
    latest_price DECIMAL(10,3),
    market_value DECIMAL(15,2),
    unrealized_pnl DECIMAL(15,2),
    unrealized_pnl_pct DECIMAL(6,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol)
);

-- 交易记录表
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE,
    user_id TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    order_type TEXT NOT NULL CHECK(order_type IN ('MARKET', 'LIMIT', 'ESTIMATED')),
    quantity INTEGER NOT NULL,
    price DECIMAL(10,3) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    filled_qty INTEGER DEFAULT 0,
    filled_amount DECIMAL(15,2) DEFAULT 0,
    fill_price DECIMAL(10,3),
    lock_price DECIMAL(10,3),
    fee DECIMAL(10,2) DEFAULT 0,
    realized_pnl DECIMAL(15,2),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELLED', 'REJECTED', 'ACCEPTED')),
    is_trading_time BOOLEAN NOT NULL DEFAULT 1,
    estimated_note TEXT,
    cancel_reason TEXT,
    t1_restricted BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    traded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 锁定状态表（挂单资金/持仓冻结，持久化）
CREATE TABLE IF NOT EXISTS locked_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    state_key TEXT NOT NULL,
    state_value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, state_key)
);

-- 持仓历史快照表
CREATE TABLE IF NOT EXISTS portfolio_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    snapshot_date DATE NOT NULL,
    total_market_value DECIMAL(15,2),
    total_cost DECIMAL(15,2),
    total_pnl DECIMAL(15,2),
    total_pnl_pct DECIMAL(6,2),
    position_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, snapshot_date)
);

-- Agent 记忆缓存表
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    agent_key TEXT NOT NULL,
    symbol TEXT,
    query_hash TEXT NOT NULL,
    query TEXT,
    result TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_lookup ON agent_memory(user_id, agent_key, query_hash);

-- Agent 链路追踪表（L5 可观测性：每次 Agent 执行的耗时/状态）
CREATE TABLE IF NOT EXISTS agent_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    conversation_id TEXT,
    user_id TEXT NOT NULL DEFAULT 'default',
    agent TEXT NOT NULL,
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'ok',  -- ok | failed | retried
    duration_ms INTEGER DEFAULT 0,
    token_used INTEGER DEFAULT 0,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_traces_trace ON agent_traces(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_agent ON agent_traces(agent);

-- 索引
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_portfolio_history_user_date ON portfolio_history(user_id, snapshot_date);

-- 对话历史表
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'default',
    title TEXT DEFAULT '新对话',
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'agent_log')),
    agent_name TEXT,
    agent_emoji TEXT,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_conv ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_messages_created ON conversation_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

-- 挂单表（非交易时段）
CREATE TABLE IF NOT EXISTS pending_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL CHECK(quantity >= 100 AND quantity % 100 = 0),
    price REAL NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'LIMIT',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'CONFIRMED', 'CANCELLED', 'EXECUTED')),
    trade_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TIMESTAMP,
    executed_at TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE INDEX IF NOT EXISTS idx_pending_orders_user ON pending_orders(user_id, status);

-- 消息反馈表（用户对 Agent 输出的点赞/倒赞）
CREATE TABLE IF NOT EXISTS message_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    conversation_id TEXT,
    agent TEXT,
    content TEXT,
    content_hash TEXT NOT NULL,
    feedback TEXT NOT NULL CHECK(feedback IN ('up', 'down')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_message_feedback_user ON message_feedback(user_id, updated_at DESC);

-- 定时任务表（Agent 制定并调度的计划任务）
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    agent_key TEXT NOT NULL DEFAULT 'chief_strategist',
    prompt TEXT NOT NULL,
    schedule_type TEXT NOT NULL DEFAULT 'interval',  -- interval | daily
    interval_seconds INTEGER NOT NULL DEFAULT 3600,
    daily_time TEXT,                                 -- 'HH:MM'（schedule_type=daily 时使用）
    status TEXT NOT NULL DEFAULT 'active',           -- active | paused
    last_run_at TEXT,
    next_run_at TEXT,
    last_result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user ON scheduled_tasks(user_id, status);

-- 用户画像表（引导页配置：昵称/头像/风险偏好/引导完成状态）
CREATE TABLE IF NOT EXISTS user_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default' UNIQUE,
    nickname TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    risk_level TEXT NOT NULL DEFAULT 'balanced',  -- conservative | balanced | aggressive
    risk_score INTEGER NOT NULL DEFAULT 0,
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_user ON user_profiles(user_id);

"""业务服务层。

按职责分组（详见 docs/ARCHITECTURE.md）：

- 行情与数据：data_source_manager / tencent_api / sina_api / live_prices /
  data_feed / market_tool / kline_cache / stock_sync / stock_lookup /
  indices / symbol
- 交易与结算：position_service / order_engine / auction_engine /
  trade_rules / fee_calculator / trading_time / portfolio_monitor_bg
- 分析与预测：technical_analysis / quant_signals / quant_prediction / predictions
- 记忆与检索：agent_memory / rag_service / embedding / news_service
- LLM 与评估：llm / llm_judge / session_manager
- 基础设施：db / cache / scheduler / task_manager / logging_config
"""

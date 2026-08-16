"""Unit tests for DataFeed / DataSnapshot."""


class TestDataSnapshot:
    """Tests for DataSnapshot construction and to_context_text."""

    @staticmethod
    def _make_snapshot():
        from backend.services.data_feed import DataSnapshot

        return DataSnapshot()

    def test_default_construction(self):
        snap = self._make_snapshot()

        assert snap.timestamp == ""
        assert snap.account is None
        assert snap.positions == []
        assert snap.active_orders == []
        assert snap.watchlist == []
        assert snap.live_prices == {}
        assert snap.indices == {}
        assert snap.is_trading is False
        assert snap.data_sources == {}
        assert isinstance(snap.to_context_text(), str)

    def test_to_context_text_with_account(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.account = {
            "balance": 100000.0,
            "total_assets": 150000.0,
        }
        snap.data_sources["account"] = "db"

        text = snap.to_context_text()
        assert "[数据快照 2025-01-15T10:00:00+08:00]" in text
        assert "可用100000.00" in text
        assert "总资产150000.00" in text

    def test_to_context_text_empty_holdings(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"

        text = snap.to_context_text()
        assert "空仓" in text

    def test_to_context_text_with_positions(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.positions = [
            {
                "symbol": "sh600519",
                "name": "贵州茅台",
                "quantity": 100,
                "avg_cost": 1600.0,
                "latest_price": 1650.0,
            },
        ]
        snap.data_sources["positions"] = "db"

        text = snap.to_context_text()
        assert "贵州茅台" in text
        assert "sh600519" in text
        assert "持仓100股" in text

    def test_to_context_text_with_orders(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.active_orders = [
            {
                "side": "buy",
                "symbol": "sh600519",
                "price": 1600.0,
                "quantity": 100,
            },
        ]

        text = snap.to_context_text()
        assert "买sh600519" in text
        assert "1600.0x100" in text

    def test_to_context_text_with_watchlist(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.watchlist = [
            {"name": "茅台", "symbol": "sh600519"},
            {"name": "平安", "symbol": "sh601318"},
        ]

        text = snap.to_context_text()
        assert "茅台(sh600519)" in text
        assert "平安(sh601318)" in text

    def test_to_context_text_with_live_prices_in_position_pnl(self):
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.positions = [
            {
                "symbol": "sh600519",
                "name": "茅台",
                "quantity": 100,
                "avg_cost": 1600.0,
                "latest_price": 1650.0,
            },
        ]
        snap.live_prices = {"sh600519": {"last_price": 1700.0}}
        snap.data_sources["positions"] = "db"

        text = snap.to_context_text()
        assert "现价1700.00" in text

    def test_to_context_text_uses_latest_price_fallback(self):
        """When live_prices is empty, fall back to position's latest_price."""
        snap = self._make_snapshot()
        snap.timestamp = "2025-01-15T10:00:00+08:00"
        snap.positions = [
            {
                "symbol": "sh600519",
                "name": "茅台",
                "quantity": 100,
                "avg_cost": 1600.0,
                "latest_price": 1650.0,
            },
        ]
        snap.data_sources["positions"] = "db"

        text = snap.to_context_text()
        assert "现价1650.00" in text

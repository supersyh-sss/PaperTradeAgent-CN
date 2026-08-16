"""交易时间判断器 — 含集合竞价阶段"""
from datetime import datetime, time, timedelta, timezone, date
from enum import Enum
from typing import Optional

try:
    import chinese_calendar as cc
except Exception:  # pragma: no cover
    cc = None

# 中国时区 UTC+8
CHINA_TZ = timezone(timedelta(hours=8))


class AuctionPhase(Enum):
    """集合竞价阶段枚举"""
    CLOSED = "closed"                      # 非竞价时段
    AUCTION_ORDER = "auction_order"        # 9:15-9:20 竞价委托（可挂可撤）
    AUCTION_LOCKED = "auction_locked"      # 9:20-9:25 竞价锁定（可挂不可撤）
    AUCTION_MATCHING = "auction_matching"  # 9:25 竞价撮合瞬间（产生开盘价）
    TRANSITION = "transition"              # 9:25-9:30 过渡期（可挂可撤，排队等开盘）


class TradingTimeChecker:
    """A股交易时间判断（接入中国法定节假日历）+ 集合竞价阶段"""

    # ── 集合竞价时间点 ──
    AUCTION_START = time(9, 15)          # 9:15 集合竞价开始
    AUCTION_LOCK_TIME = time(9, 20)      # 9:20 起不可撤单
    AUCTION_END = time(9, 25)            # 9:25 集合竞价撮合
    TRANSITION_END = time(9, 30)         # 9:30 连续竞价开始

    # ── 连续竞价时段 ──
    MORNING_START = time(9, 30)
    MORNING_END = time(11, 30)
    AFTERNOON_START = time(13, 0)
    AFTERNOON_END = time(15, 0)

    # 兜底：当 chinese-calendar 不支持的年份或导入失败时使用
    HOLIDAYS_FALLBACK = {
        "2026-01-01", "2026-01-02",  # 元旦
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",  # 春节
        "2026-04-06",  # 清明节
        "2026-05-01", "2026-05-04", "2026-05-05",  # 劳动节
        "2026-06-22",  # 端午节
        "2026-09-28",  # 中秋节
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",  # 国庆节
    }

    # chinese_calendar 返回的英文节日名 -> 中文名
    HOLIDAY_NAMES_CN = {
        "New Year's Day": "元旦",
        "Spring Festival": "春节",
        "Tomb-Sweeping Day": "清明节",
        "Labour Day": "劳动节",
        "Dragon Boat Festival": "端午节",
        "Mid-Autumn Festival": "中秋节",
        "National Day": "国庆节",
    }

    @classmethod
    def _now(cls) -> datetime:
        return datetime.now(CHINA_TZ)

    @classmethod
    def _is_workday(cls, d: date) -> bool:
        """判断是否为工作日（周末调休也算工作日）"""
        if cc is not None:
            try:
                return cc.is_workday(d)
            except Exception:
                pass
        # fallback：周末不算工作日，法定假日不算工作日
        if d.weekday() >= 5:
            return False
        return d.isoformat() not in cls.HOLIDAYS_FALLBACK

    @classmethod
    def is_weekday(cls) -> bool:
        """判断是否为工作日（周一至周五，不含调休判断）"""
        return cls._now().weekday() < 5

    @classmethod
    def is_holiday(cls) -> bool:
        """判断今天是否为法定节假日"""
        today = cls._now().date()
        if cc is not None:
            try:
                return cc.is_holiday(today)
            except Exception:
                pass
        return today.isoformat() in cls.HOLIDAYS_FALLBACK

    @classmethod
    def is_trading_day(cls) -> bool:
        """判断今天是否为交易日"""
        return cls._is_workday(cls._now().date())

    @classmethod
    def is_trading_time(cls) -> bool:
        """判断当前是否在交易时间内"""
        if not cls.is_trading_day():
            return False
        now_time = cls._now().time()
        return (cls.MORNING_START <= now_time <= cls.MORNING_END or
                cls.AFTERNOON_START <= now_time <= cls.AFTERNOON_END)

    @classmethod
    def get_next_trading_day(cls, after: Optional[date] = None) -> datetime:
        """获取下一个交易日（默认从明天开始）"""
        base = after or cls._now().date()
        next_day = base + timedelta(days=1)
        for _ in range(60):
            if cls._is_workday(next_day):
                return datetime.combine(next_day, time.min).replace(tzinfo=CHINA_TZ)
            next_day = next_day + timedelta(days=1)
        # 兜底：返回 60 天后的日期，避免死循环
        return datetime.combine(next_day, time.min).replace(tzinfo=CHINA_TZ)

    @classmethod
    def get_refresh_interval(cls) -> int:
        """获取持仓监控刷新间隔（秒）"""
        return 30 if cls.is_trading_time() else 300

    # ── 集合竞价阶段判断 ──

    @classmethod
    def get_auction_phase(cls) -> AuctionPhase:
        """返回当前集合竞价阶段"""
        if not cls.is_trading_day():
            return AuctionPhase.CLOSED
        now_time = cls._now().time()

        if cls.AUCTION_START <= now_time < cls.AUCTION_LOCK_TIME:
            return AuctionPhase.AUCTION_ORDER
        elif cls.AUCTION_LOCK_TIME <= now_time < cls.AUCTION_END:
            return AuctionPhase.AUCTION_LOCKED
        elif cls.AUCTION_END <= now_time < cls.TRANSITION_END:
            return AuctionPhase.TRANSITION
        return AuctionPhase.CLOSED

    @classmethod
    def is_auction_phase(cls) -> bool:
        """是否在集合竞价阶段（9:15-9:30）"""
        return cls.get_auction_phase() != AuctionPhase.CLOSED

    @classmethod
    def is_auction_cancellable(cls) -> bool:
        """当前是否可以撤单：竞价委托期 + 过渡期可撤，竞价锁定期不可撤"""
        phase = cls.get_auction_phase()
        return phase in (AuctionPhase.AUCTION_ORDER, AuctionPhase.TRANSITION, AuctionPhase.CLOSED)

    @classmethod
    def is_order_acceptable(cls) -> bool:
        """当前是否可以接受新订单（含竞价阶段）"""
        if not cls.is_trading_day():
            return False
        now_time = cls._now().time()
        # 9:15-11:30 + 13:00-15:00 均接受订单（含竞价、连续竞价）
        return ((cls.AUCTION_START <= now_time <= cls.MORNING_END) or
                (cls.AFTERNOON_START <= now_time <= cls.AFTERNOON_END))

    @classmethod
    def auction_active_info(cls) -> dict:
        """返回竞价阶段详情，供 Agent 和前端使用"""
        phase = cls.get_auction_phase()
        if phase == AuctionPhase.CLOSED:
            return {
                "is_auction": False,
                "phase": "closed",
                "can_order": False,
                "can_cancel": False,
                "can_match": False,
                "description": "非集合竞价时段",
            }

        info = {
            "is_auction": True,
            "phase": phase.value,
        }

        phase_config = {
            AuctionPhase.AUCTION_ORDER: {
                "can_order": True, "can_cancel": True, "can_match": False,
                "description": "集合竞价委托期（9:15-9:20）— 可挂单可撤单，订单累积不成交",
            },
            AuctionPhase.AUCTION_LOCKED: {
                "can_order": True, "can_cancel": False, "can_match": False,
                "description": "集合竞价锁定期（9:20-9:25）— 可挂单不可撤单，9:25 统一撮合",
            },
            AuctionPhase.AUCTION_MATCHING: {
                "can_order": False, "can_cancel": False, "can_match": True,
                "description": "集合竞价撮合（9:25）— 产生开盘价",
            },
            AuctionPhase.TRANSITION: {
                "can_order": True, "can_cancel": True, "can_match": False,
                "description": "开盘过渡期（9:25-9:30）— 可挂单可撤单，订单排队等9:30连续竞价",
            },
        }

        info.update(phase_config.get(phase, {}))
        return info

    @classmethod
    def trading_status_info(cls) -> dict:
        """返回交易状态信息（含集合竞价阶段）"""
        now = cls._now()
        is_trading = cls.is_trading_time()
        status = "trading" if is_trading else "closed"

        auction = cls.auction_active_info()
        phase = cls.get_auction_phase()

        if not cls.is_trading_day():
            detail = "非交易日（周末或节假日）"
        elif phase != AuctionPhase.CLOSED:
            detail = auction["description"]
            status = "auction"
        elif now.time() < cls.AUCTION_START:
            detail = "尚未开盘，等待 9:15 集合竞价"
        elif cls.MORNING_END < now.time() < cls.AFTERNOON_START:
            detail = "中午休市，等待 13:00 开盘"
        elif now.time() > cls.AFTERNOON_END:
            detail = "已收盘"
        else:
            detail = "交易中"
            if cls.MORNING_START <= now.time() <= cls.MORNING_END:
                detail += "（早盘）"
            else:
                detail += "（午盘）"

        return {
            "status": status,
            "is_trading": is_trading,
            "detail": detail,
            "auction": auction,
            "refresh_interval": cls.get_refresh_interval(),
            "next_trading_day": cls.get_next_trading_day().strftime("%Y-%m-%d"),
        }

    @classmethod
    def market_status_text(cls) -> str:
        """返回用于注入 Agent 上下文的简洁中文交易状态描述（含今天是否休市）。"""
        info = cls.trading_status_info()
        next_day = info["next_trading_day"]
        if not cls.is_trading_day():
            return f"今天休市（{info['detail']}），下一交易日为 {next_day}"
        if info["status"] == "auction":
            return "今天是交易日，当前处于集合竞价阶段"
        if info["status"] == "trading":
            return "今天是交易日，当前交易中"
        return f"今天是交易日，当前{info['detail']}"

    @classmethod
    def full_time_context(cls) -> str:
        """返回注入 Agent 上下文的完整时间描述。

        一次性包含：年/月/日/时/分（北京时间）、星期几、是否节假日（含节日名）、
        是否休市、当前交易时段、下一交易日。所有 Agent 共用此描述，避免时间信息残缺。
        """
        now = cls._now()
        weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
        date_text = now.strftime("%Y年%m月%d日 %H:%M")

        # 法定节假日名称：优先 chinese_calendar，缺失时用 fallback 集合兜底
        holiday_name = ""
        if cc is not None:
            try:
                is_hol, name = cc.get_holiday_detail(now.date())
                if is_hol and name:
                    holiday_name = cls.HOLIDAY_NAMES_CN.get(str(name), str(name))
            except Exception:
                holiday_name = ""
        if not holiday_name and now.date().isoformat() in cls.HOLIDAYS_FALLBACK:
            holiday_name = "法定节假日"

        info = cls.trading_status_info()

        if holiday_name:
            day_desc = f"今天是法定节假日（{holiday_name}），休市"
        elif cls.is_trading_day():
            day_desc = "今天是交易日"
        else:
            day_desc = "今天是周末休市日"

        return (
            f"现在是北京时间 {date_text}（{weekday_cn}）。{day_desc}。"
            f"当前交易时段：{info['detail']}。下一交易日：{info['next_trading_day']}。"
        )

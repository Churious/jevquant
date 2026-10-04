"""Regional regular sessions and explicit holiday exclusions; native quote currencies."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .market import aware, is_stale, MINUTES


def session_bounds(symbol, timestamp, cfg):
    us = symbol in cfg.market.us_stock_symbols
    local = aware(timestamp).astimezone(
        ZoneInfo("America/New_York" if us else "Asia/Seoul")
    )
    holidays = cfg.market.us_holidays if us else cfg.market.kr_holidays
    if local.weekday() >= 5 or local.date().isoformat() in holidays:
        return None
    start = local.replace(hour=9, minute=30 if us else 0, second=0, microsecond=0)
    end = local.replace(
        hour=16 if us else 15, minute=0 if us else 30, second=0, microsecond=0
    )
    return start, end


def session_open(symbol, timestamp, cfg):
    bounds = session_bounds(symbol, timestamp, cfg)
    return bool(bounds and bounds[0] <= aware(timestamp) < bounds[1])


def session_exit_due(symbol, timestamp, cfg):
    bounds = session_bounds(symbol, timestamp, cfg)
    return bool(bounds and aware(timestamp) >= bounds[1] - timedelta(minutes=10))


def price_factor(symbol, cfg):
    if cfg.trading.base_currency == "KRW" and symbol in cfg.market.us_stock_symbols:
        if not cfg.trading.usd_krw:
            raise ValueError("USD/KRW conversion is required")
        return cfg.trading.usd_krw
    return 1.0


def account_bar(bar, cfg):
    factor = price_factor(bar.symbol, cfg)
    return bar.model_copy(
        update={
            key: getattr(bar, key) * factor for key in ("open", "high", "low", "close")
        }
    )


def window_status(cfg, now):
    settings = cfg.tournament
    if not settings or not settings.scheduled_start_at:
        return None
    if aware(now) < datetime.fromisoformat(settings.scheduled_start_at):
        return "SCHEDULED_WAIT"
    if aware(now) >= datetime.fromisoformat(settings.scheduled_end_at):
        return "TOURNAMENT_ENDED"
    return None


def context_fresh(bar, now, cfg):
    if not is_stale(
        bar, now, cfg.market.stale_multiplier, cfg.market.future_tolerance_seconds
    ):
        return True
    if "/" in bar.symbol or bar.timeframe == "1m":
        return False
    bounds = session_bounds(bar.symbol, now, cfg)
    if not bounds or not bounds[0] <= aware(now) < bounds[0] + timedelta(
        minutes=MINUTES[bar.timeframe]
    ):
        return False
    # At the open, the immediately preceding trading session is valid higher-TF context.
    previous = bounds[0] - timedelta(days=1)
    while not session_bounds(bar.symbol, previous, cfg):
        previous -= timedelta(days=1)
    prior_end = session_bounds(bar.symbol, previous, cfg)[1]
    local_end = bar.end.astimezone(prior_end.tzinfo)
    return local_end.date() == previous.date() and timedelta(
        0
    ) <= prior_end - local_end < timedelta(minutes=MINUTES[bar.timeframe])

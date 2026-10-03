from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.jev import JevDecision
from app.market import Bar, FeatureSnapshot, store_bar
from app.research import (
    ForwardReturn,
    calibration,
    state_hash,
    stats,
    trading_metrics,
    update_forward_returns,
)
from app.replay import replay
from app.trading import PaperOrder
from test_jev import response
from test_trading import setup


def test_shadow_hold_returns_are_matured_and_idempotent(db):
    cfg, account, bar, s = setup(db)
    cfg.market.execution_timeframe = "5m"
    cfg.research.forward_minutes = [5, 15, 60, 240, 1440]
    f = db.get(FeatureSnapshot, s.feature_id)
    f.state = {"symbol": bar.symbol, "regime": "TRENDING_UP"}
    origin = bar.end
    d = JevDecision(
        feature_id=f.id,
        timestamp=origin,
        request={},
        raw_response=response(0.67),
        attempts=[],
        model_version="jev-1.13.0",
        status="OK",
        latency_ms=1,
        request_count=1,
        estimated_cost=0,
    )
    db.add(d)
    db.flush()
    later = bar.model_copy(
        update={"timestamp": origin, "open": 100, "low": 100, "high": 102, "close": 102}
    )
    store_bar(db, later, "test")
    update_forward_returns(db, "test", origin, cfg)
    db.flush()
    assert not list(db.scalars(select(ForwardReturn)))
    update_forward_returns(db, "test", later.end, cfg)
    db.flush()
    update_forward_returns(db, "test", later.end, cfg)
    db.flush()
    rows = list(db.scalars(select(ForwardReturn)))
    assert len(rows) == 1
    assert rows[0].horizon_minutes == 5
    assert rows[0].return_value == pytest.approx(0.02)
    buckets = calibration(db, "test", cfg, horizon=5)["buckets"]
    assert buckets[1]["sample_count"] == 1
    assert buckets[1]["mean_return"] == pytest.approx(0.02)


def test_metrics_not_fabricated_on_tiny_sample():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    snaps = [
        SimpleNamespace(timestamp=now, equity=10000),
        SimpleNamespace(timestamp=now + timedelta(hours=1), equity=9900),
    ]
    trades = [SimpleNamespace(pnl=100), SimpleNamespace(pnl=-50)]
    m = trading_metrics(snaps, trades, 10000)
    assert m["total_return"] == pytest.approx(-0.01)
    assert m["max_drawdown"] == pytest.approx(0.01)
    assert m["profit_factor"] == 2
    assert m["expectancy"] == 25
    assert m["sharpe_ratio"] is None
    assert m["annualized_return"] is None
    assert stats([])["mean_return"] is None
    assert stats([0.01, 0.03])["median_return"] == pytest.approx(0.02)


def replay_bars():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for symbol in ["BTC/USDT", "ETH/USDT"]:
        for tf, minutes in [("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60)]:
            for i in range(72 * 60 // minutes):
                price = 100 + i * 0.01
                bars.append(
                    Bar(
                        symbol=symbol,
                        timeframe=tf,
                        timestamp=start + timedelta(minutes=i * minutes),
                        open=price,
                        high=price + 0.2,
                        low=price - 0.2,
                        close=price + 0.05,
                        volume=10,
                    )
                )
    return bars


def test_replay_engine_offline_reproducible_and_run_immutable(db):
    from app.config import load_config

    cfg = load_config()
    cfg.market.timeframes = ["5m", "15m", "1h"]
    cfg.market.execution_timeframe = "5m"
    cfg.strategy.timeframe = "15m"
    bars = replay_bars()
    r1 = replay(db, bars, {}, cfg, "replay1")
    r2 = replay(db, list(reversed(bars)), {}, cfg, "replay2")
    assert r1["portfolios"] == r2["portfolios"]
    assert r1["portfolios"]["jev"]["trade_count"] == 0
    assert r1["portfolios"]["buy_hold"]["total_return"] != 0
    assert r1["cached_decisions_used"] == 0
    with pytest.raises(ValueError, match="already exists"):
        replay(db, bars, {}, cfg, "replay1")
    orders = list(db.scalars(select(PaperOrder)))
    assert all(o.side in {"BUY", "SELL", "SHORT", "COVER"} for o in orders)


def test_state_hash_canonical():
    assert state_hash({"b": 2, "a": 1}) == state_hash({"a": 1, "b": 2})
    assert state_hash({"a": 1}) != state_hash({"a": 2})

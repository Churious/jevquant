from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import load_config
from app.db import Base
from app.jev import StrategyDecision
from app.market import Bar, Candle, FeatureSnapshot
from app.trading import (
    Account,
    PaperBroker,
    PaperOrder,
    PaperTrade,
    Position,
    ensure_run,
    equity,
    position_size,
    strategy_action,
    update_daily,
)
from test_jev import response


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def setup(db, side="LONG"):
    cfg = load_config()
    cfg.trading.starting_capital = 10000  # small isolated ledger fixture
    ensure_run(db, "test", "REPLAY", cfg, "hash")
    account = db.scalar(select(Account).where(Account.strategy == "jev"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bar = Bar(
        symbol="BTC/USDT",
        timeframe="5m",
        timestamp=now,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
    )
    candle = Candle(**bar.model_dump(), provider="test")
    db.add(candle)
    db.flush()
    feature = FeatureSnapshot(
        run_id="test", candle_id=candle.id, timestamp=now, state={}, config_hash="hash"
    )
    db.add(feature)
    db.flush()
    decision = StrategyDecision(
        run_id="test",
        feature_id=feature.id,
        timestamp=now,
        strategy="jev",
        strategy_version="v1",
        action=side,
        reason="test",
    )
    db.add(decision)
    db.flush()
    return cfg, account, bar, decision


def test_position_sizing_limits():
    cfg = load_config()
    qty, distance = position_size(10000, 10000, 100, 2, cfg)
    assert distance == 3
    assert qty * 100 <= 1000
    assert qty * (distance + 2 * 100 * 0.001 + 100 * 0.0005) <= 50
    assert position_size(10000, 0, 100, 2, cfg)[0] == 0


@pytest.mark.parametrize("side,exit_price", [("LONG", 110), ("SHORT", 90)])
def test_execution_fees_slippage_pnl_and_ledger(db, side, exit_price):
    cfg, account, bar, decision = setup(db, side)
    broker = PaperBroker(cfg)
    positions = []
    assert (
        broker.open(db, account, positions, decision, bar, {"volatility": {"atr14": 2}})
        == "EXECUTED"
    )
    p = positions[0]
    entry = broker.fill(100, "BUY" if side == "LONG" else "SHORT")
    assert p.entry_price == entry
    assert p.entry_fee == pytest.approx(entry * p.quantity * 0.001)
    assert equity(account, positions) == pytest.approx(
        10000 - p.entry_fee + (100 - entry) * p.quantity * (1 if side == "LONG" else -1)
    )
    broker.close(db, account, p, decision, bar.end, exit_price, "test_close")
    db.flush()
    trade = db.scalar(select(PaperTrade))
    assert trade.pnl > 0
    assert account.cash == pytest.approx(10000 + trade.pnl)
    assert len(list(db.scalars(select(PaperOrder)))) == 2
    assert len(list(db.scalars(select(Position)))) == 0


def test_stop_take_and_adverse_gap(db):
    cfg, account, bar, decision = setup(db)
    broker = PaperBroker(cfg)
    positions = []
    broker.open(db, account, positions, decision, bar, {"volatility": {"atr14": 2}})
    p = positions[0]
    both = bar.model_copy(update={"low": 90, "high": 120})
    assert broker.barrier(p, both)[0] == "STOP_LOSS"
    gap = bar.model_copy(update={"open": 90, "low": 89, "close": 90})
    assert broker.barrier(p, gap) == ("STOP_LOSS", 90)
    take = bar.model_copy(update={"high": 120})
    assert broker.barrier(p, take)[0] == "TAKE_PROFIT"


def test_daily_limit_latches_until_next_day(db):
    cfg, account, bar, _ = setup(db)
    update_daily(account, [], bar.timestamp, cfg)
    account.cash = 9799
    assert update_daily(account, [], bar.end, cfg)
    account.cash = 10000
    assert update_daily(account, [], bar.end, cfg)
    assert not update_daily(account, [], bar.end + timedelta(days=1), cfg)


def test_deterministic_gating_and_no_fallback():
    cfg = load_config()
    state = {"higher_timeframe": {"trend_1h": "up"}}
    assert strategy_action("jev", state, None, cfg) == ("HOLD", "JEV_UNAVAILABLE")
    d = SimpleNamespace(status="OK", raw_response=response())
    assert strategy_action("jev", state, d, cfg)[0] == "LONG"
    d.raw_response = response(0.67)
    assert strategy_action("jev", state, d, cfg)[0] == "HOLD"
    d.raw_response = response()
    state["higher_timeframe"]["trend_1h"] = "strong_down"
    assert strategy_action("jev", state, d, cfg)[0] == "HOLD"

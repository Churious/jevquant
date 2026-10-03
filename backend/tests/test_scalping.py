from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import load_config, Config
from app.features import build_state
from app.jev import JevDecision, StrategyDecision
from app.market import FeatureSnapshot, store_bar
from app.replay import replay
from app.research import ForwardReturn, update_forward_returns
from app.runner import Runner
from app.trading import (
    PaperBroker,
    PaperOrder,
    PaperTrade,
    Position,
    process_execution_batch,
)
from test_features import histories
from test_trading import setup


def test_minute_features_exclude_unfinished_context():
    cfg = load_config()
    data, now = histories()
    state = build_state("BTC/USDT", "1m", data, now, cfg)
    assert state["timeframe"] == "1m"
    assert state["decision_context"]["return_period_unit_minutes"] == 1
    assert state["price"]["return_3"] == pytest.approx(199 / 196 - 1)
    data["1m"].append(
        data["1m"][-1].model_copy(update={"timestamp": now, "close": 999})
    )
    assert build_state("BTC/USDT", "1m", data, now, cfg) == state


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_cost_filter_rejects_small_targets_without_orders(db, side):
    cfg, account, bar, signal = setup(db, side)
    positions = []
    assert (
        PaperBroker(cfg).open(
            db, account, positions, signal, bar, {"volatility": {"atr14": 0.01}}
        )
        == "COST_FILTER"
    )
    assert not positions and not list(db.scalars(select(PaperOrder)))
    assert account.cash == 10000


def test_timed_exit_and_no_same_bar_reentry(db):
    cfg, account, bar, signal = setup(db)
    bar = bar.model_copy(update={"timeframe": "1m"})
    feature = db.get(FeatureSnapshot, signal.feature_id)
    feature.state = {"symbol": bar.symbol, "volatility": {"atr14": 2}}
    PaperBroker(cfg).open(db, account, [], signal, bar, feature.state)
    signal.status = "EXECUTED"
    later = bar.model_copy(update={"timestamp": bar.timestamp + timedelta(minutes=10)})
    retry = StrategyDecision(
        run_id="test",
        feature_id=feature.id,
        timestamp=later.timestamp,
        strategy="jev",
        strategy_version="v2",
        action="LONG",
        reason="test",
    )
    db.add(retry)
    db.flush()
    process_execution_batch(db, "test", [later], cfg)
    assert not list(db.scalars(select(Position)))
    assert db.scalar(select(PaperTrade)).reason == "TIME_EXIT"
    assert retry.status == "COOLDOWN"


def test_minute_execution_waits_until_signal_available(db):
    cfg, account, bar, signal = setup(db)
    bar = bar.model_copy(update={"timeframe": "1m"})
    feature = db.get(FeatureSnapshot, signal.feature_id)
    feature.state = {"symbol": bar.symbol, "volatility": {"atr14": 2}}
    signal.timestamp = bar.timestamp + timedelta(seconds=1)
    process_execution_batch(db, "test", [bar], cfg)
    assert not list(db.scalars(select(PaperOrder)))
    process_execution_batch(
        db, "test", [bar.model_copy(update={"timestamp": bar.end})], cfg
    )
    assert (
        db.scalar(select(PaperOrder)).timestamp.replace(tzinfo=bar.timestamp.tzinfo)
        >= signal.timestamp
    )


def test_one_minute_shadow_returns_mature_after_response(db):
    cfg, account, bar, signal = setup(db)
    bar = bar.model_copy(update={"timeframe": "1m"})
    store_bar(db, bar, "test")
    feature = db.get(FeatureSnapshot, signal.feature_id)
    feature.state = {"symbol": bar.symbol}
    decision = JevDecision(
        feature_id=feature.id,
        timestamp=bar.end,
        request={},
        raw_response=None,
        attempts=[],
        model_version=None,
        status="JEV_UNAVAILABLE",
        latency_ms=0,
        request_count=0,
        estimated_cost=None,
    )
    db.add(decision)
    db.flush()
    later = bar.model_copy(update={"timestamp": bar.end, "high": 102, "close": 102})
    store_bar(db, later, "test")
    update_forward_returns(db, "test", bar.end, cfg)
    db.flush()
    assert not list(db.scalars(select(ForwardReturn)))
    update_forward_returns(db, "test", later.end, cfg)
    db.flush()
    result = db.scalar(select(ForwardReturn))
    assert result.horizon_minutes == 1 and result.return_value == pytest.approx(0.02)


async def test_slow_context_is_cached_until_next_close():
    runner = Runner()
    data, _ = histories()
    from app.db import utcnow

    now = utcnow()
    bar = data["1h"][-1].model_copy(update={"timestamp": now - timedelta(minutes=30)})
    calls = []

    async def history(symbol, tf, limit):
        calls.append(tf)
        return [bar.model_copy(update={"timestamp": now - timedelta(hours=1)})]

    provider = SimpleNamespace(name="test", history=history)
    first, refreshed = await runner.history(provider, "BTC/USDT", "1h")
    second, refreshed_again = await runner.history(provider, "BTC/USDT", "1h")
    assert refreshed and not refreshed_again and first == second and calls == ["1h"]


def test_minute_replay_uses_same_execution_resolution(db):
    cfg = load_config()
    data, now = histories()
    bars = [bar for group in data.values() for bar in group]
    replay(db, bars, {}, cfg, "minute-replay")
    signals = list(
        db.scalars(
            select(StrategyDecision).where(StrategyDecision.run_id == "minute-replay")
        )
    )
    assert len(signals) > 4
    orders = list(db.scalars(select(PaperOrder)))
    assert orders
    assert any(order.timestamp.minute % 5 != 0 for order in orders)


def test_legacy_experiment_config_remains_readable():
    raw = load_config().model_dump()
    raw["market"]["timeframes"] = ["5m", "15m", "1h"]
    raw["market"].pop("execution_timeframe")
    raw["strategy"]["timeframe"] = "15m"
    raw["strategy"].pop("max_holding_minutes")
    raw["simulation"].pop("min_net_target_return")
    cfg = Config.model_validate(raw)
    assert (
        cfg.market.execution_timeframe == "5m" and cfg.strategy.max_holding_minutes == 0
    )


async def test_retry_after_exceeding_scalp_budget_abstains(monkeypatch):
    import httpx
    from app.jev import JevClient

    monkeypatch.setenv("TYPESAFE_API_KEY", "mock-only")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, json={"error": "limited"}, headers={"retry-after": "60"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevClient(client, load_config()).evaluate({})
    assert len(calls) == 1 and result["status"] == "JEV_UNAVAILABLE"
    assert result["latency_ms"] < 1000


async def test_runner_judges_each_minute_once(db, monkeypatch):
    from contextlib import contextmanager
    import app.runner as module

    data, now = histories()
    runner = Runner()
    runner.run_id = "minute-live-test"

    @contextmanager
    def begin():
        yield db

    monkeypatch.setattr(module, "Session", SimpleNamespace(begin=begin))
    clock = [now]
    monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    calls = []

    async def evaluate(state):
        calls.append(state)
        return {
            "request": {},
            "raw_response": None,
            "attempts": [],
            "model_version": None,
            "status": "JEV_UNAVAILABLE",
            "latency_ms": 0,
            "request_count": 0,
            "estimated_cost": None,
        }

    runner.jev = SimpleNamespace(evaluate=evaluate)
    store_bar(db, data["1m"][-1], "test")
    await runner.process_symbol("BTC/USDT", data)
    db.flush()
    await runner.process_symbol("BTC/USDT", data)
    assert len(calls) == 1
    next_bar = data["1m"][-1].model_copy(update={"timestamp": now})
    data["1m"].append(next_bar)
    clock[0] = next_bar.end
    store_bar(db, next_bar, "test")
    await runner.process_symbol("BTC/USDT", data)
    db.flush()
    assert len(calls) == 2 and all(c["timeframe"] == "1m" for c in calls)
    assert len(list(db.scalars(select(JevDecision)))) == 2


def test_stock_scalping_closes_before_session_end(db):
    from app.korean_market import KST
    from datetime import datetime

    cfg, account, bar, signal = setup(db)
    bar = bar.model_copy(
        update={
            "symbol": "069500",
            "timeframe": "1m",
            "timestamp": datetime(2026, 1, 2, 15, 19, tzinfo=KST),
        }
    )
    feature = db.get(FeatureSnapshot, signal.feature_id)
    feature.state = {"symbol": bar.symbol, "volatility": {"atr14": 2}}
    PaperBroker(cfg).open(db, account, [], signal, bar, feature.state)
    signal.status = "EXECUTED"
    later = bar.model_copy(update={"timestamp": bar.end})
    process_execution_batch(db, "test", [later], cfg)
    assert not list(db.scalars(select(Position)))
    assert db.scalar(select(PaperTrade)).reason == "SESSION_EXIT"


async def test_jev_total_deadline_abstains_on_slow_transport(monkeypatch):
    import asyncio
    import httpx
    from app.jev import JevClient
    from test_jev import response

    monkeypatch.setenv("TYPESAFE_API_KEY", "mock-only")
    cfg = load_config()
    cfg.jev.timeout_seconds = 1
    cfg.jev.max_evaluation_seconds = 0.02

    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevClient(client, cfg).evaluate({})
    assert result["status"] == "JEV_UNAVAILABLE" and result["request_count"] == 1
    assert result["raw_response"] is None and result["latency_ms"] < 150

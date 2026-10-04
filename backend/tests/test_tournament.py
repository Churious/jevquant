import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.config import Config, load_config
from app.decision_runtime import DecisionRuntime
from app.decision_queue import TournamentDecisionQueue, record_action
from app.jev import JevClient, JevDecision, StrategyDecision
from app.market import Bar, FeatureSnapshot, aware, store_bar
from app.research import calibration, update_forward_returns, state_hash
from app.tournament import (
    ensure_tournament,
    start_tournament,
    finish_if_due,
    leaderboard,
    comparison,
)
from app.traders import (
    questions_for,
    policy_for,
    runtime_for,
    trader_action,
)
from app.trading import (
    Account,
    PaperBroker,
    PaperOrder,
    PortfolioSnapshot,
    account_config,
    process_execution_batch,
    snapshot_accounts,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUNTIME = DecisionRuntime("local", "http://model:11434", "tev1:0.8b", 1, 7)


def make_tournament(db, identity="test-tournament", started=NOW):
    cfg = load_config()
    t = ensure_tournament(db, identity, cfg, RUNTIME)
    if started:
        start_tournament(db, t, started, cfg)
    return cfg, t


def feature(db, t, when=NOW):
    b = Bar(
        symbol="BTC/KRW",
        timeframe="1m",
        timestamp=when - timedelta(minutes=1),
        open=100,
        high=101,
        low=99,
        close=100,
        volume=10,
    )
    candle = store_bar(db, b, "test")
    state = {
        "symbol": b.symbol,
        "regime": "TRENDING_UP",
        "volatility": {"atr14": 2},
        "trend": {"ema9": 101, "ema21": 100, "ema9_above_21": True},
        "momentum": {"rsi14": 50},
        "higher_timeframe": {},
    }
    f = FeatureSnapshot(
        run_id=t.id,
        candle_id=candle.id,
        timestamp=when,
        state=state,
        config_hash=t.configuration_hash,
    )
    db.add(f)
    db.flush()
    return f, b


def raw_response(definition, *, direction="up", risk=0, regime="TREND"):
    questions, policy = questions_for(definition), policy_for(definition)
    answers = {}
    for key, q in questions.items():
        if q["type"] == "noul":
            answers[key] = {"type": "noul", "noul": 0.9 if key == "long_setup" else 0.1}
        elif q["type"] == "choice":
            chosen = (
                regime
                if key == "market_regime"
                else "LONG"
                if key == "preferred_action"
                else direction
            )
            answers[key] = {
                "type": "choice",
                "choice": chosen,
                "confidence": 1,
                "probabilities": {k: float(k == chosen) for k in q["criteria"]},
            }
        else:
            score = risk if key == policy["risk"] else 4
            answers[key] = {
                "type": "score",
                "score": score,
                "confidence": 1,
                "probabilities": {str(i): float(i == score) for i in range(5)},
                "legend": {str(i): q["criteria"][i] for i in range(5)},
            }
    return {"model": RUNTIME.model, "answers": answers, "usage": {"input_tokens": 100}}


def signal(db, cfg, t, definition, f, observed=NOW):
    decision = JevDecision(
        tournament_id=t.id,
        trader_id=definition.id,
        feature_id=f.id,
        timestamp=observed,
        request={"questions": questions_for(definition)},
        raw_response=raw_response(definition),
        model_version="local:tev1:0.8b",
        status="OK",
        latency_ms=1000,
        request_count=1,
        estimated_cost=None,
    )
    db.add(decision)
    db.flush()
    record_action(db, f, definition, decision, cfg, observed)
    db.flush()
    return db.scalar(
        select(StrategyDecision).where(StrategyDecision.jev_decision_id == decision.id)
    )


def test_nine_independent_million_accounts_and_zero_anchors(db):
    cfg, t = make_tournament(db)
    accounts = list(db.scalars(select(Account)))
    assert len(accounts) == 9 and len({a.trader_record_id for a in accounts}) == 9
    assert all(a.cash == a.day_start_equity == 1_000_000 for a in accounts)
    assert aware(t.ends_at) - aware(t.started_at) == timedelta(days=7)
    assert all(s.equity == 1_000_000 for s in db.scalars(select(PortfolioSnapshot)))
    ranking = leaderboard(db, t, cfg, NOW)
    assert len(ranking) == 9 and all(
        r["rank"] == 1 and r["return_pct"] == 0 for r in ranking
    )


def test_isolation_orders_risk_and_configs(db):
    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    a, b_definition = cfg.traders[:2]
    sa, sb = signal(db, cfg, t, a, f), signal(db, cfg, t, b_definition, f)
    aa = db.scalar(select(Account).where(Account.strategy == a.id))
    ab = db.scalar(select(Account).where(Account.strategy == b_definition.id))
    broker = PaperBroker(account_config(db, aa, cfg))
    positions = []
    assert (
        broker.open(
            db, aa, positions, sa, b.model_copy(update={"timestamp": NOW}), f.state
        )
        == "EXECUTED"
    )
    assert aa.cash < 1_000_000 and ab.cash == 1_000_000
    assert positions[0].trader_id == a.id and positions[0].tournament_id == t.id
    aa.halted = True
    assert (
        PaperBroker(account_config(db, ab, cfg)).open(
            db, ab, [], sb, b.model_copy(update={"timestamp": NOW}), f.state
        )
        == "EXECUTED"
    )
    order = db.scalar(select(PaperOrder).where(PaperOrder.account_id == aa.id))
    assert (
        order.decision_snapshot_id == sa.id
        and order.jev_model_version == "local:tev1:0.8b"
    )
    with pytest.raises(ValueError):
        broker.order(db, aa, sb, NOW, "BTC/KRW", "BUY", 100, 1, "cross-account")
    assert not ab.halted
    own = account_config(db, aa, cfg)
    own.risk.atr_stop_multiplier = 99
    assert account_config(db, ab, cfg).risk.atr_stop_multiplier == 1.5


def test_latency_never_fills_past_open(db):
    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    s = signal(db, cfg, t, cfg.traders[0], f, NOW + timedelta(seconds=3))
    bar = b.model_copy(update={"timestamp": NOW})
    process_execution_batch(db, t.id, [bar], cfg)
    assert not db.scalar(select(PaperOrder))
    assert s.status == "PENDING"
    process_execution_batch(
        db,
        t.id,
        [bar.model_copy(update={"timestamp": NOW + timedelta(minutes=1)})],
        cfg,
    )
    order = db.scalar(select(PaperOrder))
    assert aware(order.timestamp) == NOW + timedelta(minutes=1)


@pytest.mark.parametrize("index", range(6))
def test_independent_style_questions_and_real_provider_calls(index):
    cfg = load_config()
    definition = cfg.traders[index]
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json=raw_response(definition))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await JevClient(client, cfg, RUNTIME).evaluate(
                {"symbol": "BTC/KRW"}, questions_for(definition)
            )

    result = asyncio.run(run())
    assert result["status"] == "OK" and len(seen) == 1
    assert seen[0]["questions"] == questions_for(definition)
    assert result["raw_response"] == raw_response(definition)
    assert len({state_hash(questions_for(d)) for d in cfg.traders[:6]}) == 6
    assert all(questions_for(d) == {} for d in cfg.traders[6:])


@pytest.mark.parametrize(
    "style,risk,regime,expected",
    [
        ("trend", 0, "TREND", "LONG"),
        ("mean_reversion", 4, "TREND", "HOLD"),
        ("multi_timeframe", 0, "TREND", "LONG"),
        ("adaptive", 0, "UNCERTAIN", "HOLD"),
        ("adaptive", 0, "HIGH_VOLATILITY", "HOLD"),
    ],
)
def test_style_policy(style, risk, regime, expected):
    cfg = load_config()
    d = next(d for d in cfg.traders if d.strategy == style)
    decision = SimpleNamespace(
        status="OK", raw_response=raw_response(d, risk=risk, regime=regime)
    )
    # MultiTF may permit a lower-timeframe pullback despite contradictory mechanical directions.
    assert (
        trader_action(d, {"higher_timeframe": {"trend_1h": "down"}}, decision, cfg)[0]
        == expected
    )


def test_provider_overrides_and_immutable_restart(db, monkeypatch):
    cfg, t = make_tournament(db)
    account = db.scalar(select(Account).where(Account.strategy == cfg.traders[0].id))
    account.cash = 912345
    db.flush()
    assert ensure_tournament(db, t.id, cfg, RUNTIME).id == t.id
    assert account.cash == 912345
    changed = cfg.model_copy(deep=True)
    changed.traders[0].parameters.entry_probability = 0.8
    with pytest.raises(ValueError, match="CONFIG_CHANGED"):
        ensure_tournament(db, t.id, changed, RUNTIME)
    d = cfg.traders[0].model_copy(
        update={"provider": "typesafe", "model": "jev-1.13.0"}
    )
    runtime = runtime_for(d, RUNTIME)
    monkeypatch.setenv("TYPESAFE_API_KEY", "cloud-key")
    monkeypatch.setenv("LOCAL_JEV_API_KEY", "local-key")
    assert (
        runtime.provider == "typesafe"
        and runtime.api_key == "cloud-key"
        and RUNTIME.api_key == "local-key"
    )
    assert (
        runtime.model == "jev-1.13.0" and runtime.base_url == "https://api.typesafe.ai"
    )


def test_queue_shared_state_six_decisions_baseline_no_calls_and_deadlines(db):
    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    runner = SimpleNamespace(cfg=cfg, decision_runtime=RUNTIME)
    queue = TournamentDecisionQueue(runner)
    jobs = queue.prepare(db, f, t, NOW + timedelta(seconds=3))
    assert len(jobs) == 6
    decisions = list(db.scalars(select(JevDecision)))
    assert len({d.feature_id for d in decisions}) == 1
    assert len({state_hash(d.request["questions"]) for d in decisions}) == 6
    assert all(d.request["state"] == f.state for d in decisions)
    assert all(aware(d.deadline_at) == NOW + timedelta(seconds=53) for d in decisions)
    baselines = list(db.scalars(select(StrategyDecision)))
    assert len(baselines) == 3 and all(d.jev_decision_id is None for d in baselines)
    assert queue.prepare(db, f, t, NOW + timedelta(seconds=4)) == []


def test_expired_queue_never_calls_model_or_trades(db, monkeypatch):
    import app.decision_queue as module

    cfg, t = make_tournament(db)
    f, _ = feature(db, t)
    runner = SimpleNamespace(
        cfg=cfg, decision_runtime=RUNTIME, run_id=t.id, lock=asyncio.Lock()
    )
    queue = TournamentDecisionQueue(runner)
    jobs = queue.prepare(db, f, t, NOW)
    db.commit()
    monkeypatch.setattr(module, "Session", lambda: db)
    monkeypatch.setattr(module, "utcnow", lambda: NOW + timedelta(seconds=61))
    called = []

    async def evaluate(*args):
        called.append(1)

    monkeypatch.setattr(module.JevClient, "evaluate", evaluate)
    # Exercise worker transaction API using independent sessions on the fixture's engine.
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(
        module, "Session", sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    )

    async def run():
        await queue.submit(jobs, 0)
        task = asyncio.create_task(queue.worker())
        await asyncio.wait_for(queue.queue.join(), 2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    db.expire_all()
    assert not called
    assert all(d.status == "BUDGET_EXPIRED" for d in db.scalars(select(JevDecision)))
    assert all(
        d.action == "HOLD"
        for d in db.scalars(
            select(StrategyDecision).where(
                StrategyDecision.jev_decision_id.is_not(None)
            )
        )
    )


def test_rank_comparison_sign_exposure_and_end_freeze(db):
    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    # Baseline wins; never hide underperformance of the best Jev.
    a = db.scalar(select(Account).where(Account.strategy == "baseline-buyhold"))
    a.cash += 20000
    rows = leaderboard(db, t, cfg, NOW)
    assert rows[0]["trader_id"] == "baseline-buyhold"
    assert comparison(rows)["jev_vs_best_baseline_pp"] == pytest.approx(-2)
    s = signal(db, cfg, t, cfg.traders[0], f)
    aa = db.scalar(select(Account).where(Account.strategy == cfg.traders[0].id))
    positions = []
    PaperBroker(account_config(db, aa, cfg)).open(
        db, aa, positions, s, b.model_copy(update={"timestamp": NOW}), f.state
    )
    end = aware(t.ends_at)
    store_bar(
        db,
        b.model_copy(
            update={
                "timestamp": end - timedelta(minutes=1),
                "open": 102,
                "high": 103,
                "low": 101,
                "close": 102,
            }
        ),
        "test",
    )
    store_bar(
        db,
        b.model_copy(
            update={
                "timestamp": end,
                "open": 999,
                "high": 999,
                "low": 999,
                "close": 999,
            }
        ),
        "test",
    )
    assert finish_if_due(db, t, end, cfg)
    assert positions[0].current_price == 102 and t.status == "COMPLETED"
    assert next(r for r in t.final_report["ranking"] if r["trader_id"] == aa.strategy)[
        "current_equity"
    ] == pytest.approx(aa.cash + positions[0].quantity * 102)
    assert (
        PaperBroker(account_config(db, aa, cfg)).open(
            db, aa, [], s, b.model_copy(update={"timestamp": end}), f.state
        )
        == "TOURNAMENT_ENDED"
    )
    report = copy.deepcopy(t.final_report)
    cash = aa.cash
    assert report["valuation_prices"]["BTC/KRW"]["observed_at"] == end.isoformat()
    process_execution_batch(
        db, t.id, [b.model_copy(update={"timestamp": end, "close": 999})], cfg
    )
    snapshot_accounts(db, t.id, end + timedelta(minutes=1), cfg)
    assert not finish_if_due(db, t, end + timedelta(days=1), cfg)
    assert (
        t.final_report == report
        and aa.cash == cash
        and positions[0].current_price == 102
    )
    assert not db.scalar(
        select(PortfolioSnapshot.id).where(PortfolioSnapshot.timestamp > end)
    )
    assert len(report["ranking"]) == 9 and report["fees_by_trader"][aa.strategy] > 0


def test_equal_conditions_and_declarative_risk_validation(db):
    cfg, t = make_tournament(db)
    conditions = []
    for a in db.scalars(select(Account)):
        c = account_config(db, a, cfg)
        conditions.append(
            (
                c.simulation.model_dump(),
                c.trading.risk_per_trade,
                c.trading.max_position_allocation,
                c.trading.max_positions,
                c.trading.daily_loss_limit,
            )
        )
    assert all(c == conditions[0] for c in conditions)
    raw = cfg.model_dump()
    raw["traders"][0]["risk_profile"]["risk_per_trade"] = 0.01
    with pytest.raises(ValueError, match="risk"):
        Config.model_validate(raw)
    raw = cfg.model_dump()
    raw["traders"][0]["starting_capital"] = 10000
    with pytest.raises(ValueError):
        Config.model_validate(raw)


def test_shadow_labels_and_calibration_partition(db):
    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    signal(db, cfg, t, cfg.traders[0], f)
    signal(db, cfg, t, cfg.traders[1], f)
    cfg.research.forward_minutes = [1]
    store_bar(
        db,
        b.model_copy(update={"timestamp": NOW, "open": 100, "high": 102, "close": 102}),
        "test",
    )
    update_forward_returns(db, t.id, NOW + timedelta(minutes=1), cfg)
    db.flush()
    assert calibration(db, t.id, cfg, horizon=1)["total_samples"] == 2
    assert (
        calibration(db, t.id, cfg, horizon=1, trader_id="jev-trend")["total_samples"]
        == 1
    )


def test_daily_loss_limit_is_per_account(db):
    from app.trading import update_daily

    cfg, t = make_tournament(db)
    f, b = feature(db, t)
    definition = cfg.traders[0]
    s = signal(db, cfg, t, definition, f)
    a = db.scalar(select(Account).where(Account.strategy == definition.id))
    other = db.scalar(select(Account).where(Account.strategy == cfg.traders[1].id))
    ps = []
    update_daily(a, ps, NOW, cfg)
    update_daily(other, [], NOW, cfg)
    PaperBroker(account_config(db, a, cfg)).open(
        db, a, ps, s, b.model_copy(update={"timestamp": NOW}), f.state
    )
    ps[0].current_price = 50
    update_daily(a, ps, NOW + timedelta(minutes=1), cfg)
    update_daily(other, [], NOW + timedelta(minutes=1), cfg)
    assert a.halted and not other.halted and other.cash == 1_000_000


def test_compact_model_state_keeps_features_and_original_candle_values():
    from app.features import build_state, compact_state
    from test_features import histories

    cfg = load_config()
    hs, when = histories()
    cfg.tournament.state_precision_digits = None
    original = build_state(hs["1m"][-1].symbol, "1m", hs, when, cfg)
    cfg.tournament.state_precision_digits = 6
    compact = compact_state(original, cfg.tournament.state_precision_digits)

    def same_shape(a, b):
        if isinstance(a, dict):
            assert a.keys() == b.keys()
            for key in a:
                same_shape(a[key], b[key])
        elif isinstance(a, float):
            assert b == pytest.approx(a, rel=1e-5, abs=1e-12)
        else:
            assert a == b

    same_shape(original, compact)
    assert len(json.dumps(compact)) < len(json.dumps(original))


def test_queue_workers_respect_concurrency_and_do_not_block_event_loop(db, monkeypatch):
    import app.decision_queue as module
    from sqlalchemy.orm import sessionmaker

    cfg, t = make_tournament(db)
    f, _ = feature(db, t)
    runner = SimpleNamespace(
        cfg=cfg,
        decision_runtime=RUNTIME,
        run_id=t.id,
        lock=asyncio.Lock(),
        client=object(),
    )
    queue = TournamentDecisionQueue(runner)
    jobs = queue.prepare(db, f, t, NOW)
    db.commit()
    monkeypatch.setattr(
        module, "Session", sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    )
    monkeypatch.setattr(module, "utcnow", lambda: NOW + timedelta(seconds=5))
    running = 0
    maximum = 0
    calls = []
    ticks = []

    async def evaluate(self, state, questions):
        nonlocal running, maximum
        definition = next(d for d in cfg.traders[:6] if questions_for(d) == questions)
        running += 1
        maximum = max(maximum, running)
        calls.append(definition.id)
        await asyncio.sleep(0.02)
        running -= 1
        return {
            "status": "OK",
            "raw_response": raw_response(definition),
            "model_version": "local:tev1:0.8b",
        }

    monkeypatch.setattr(module.JevClient, "evaluate", evaluate)

    async def run():
        await queue.submit(jobs, 0)
        workers = [
            asyncio.create_task(queue.worker()) for _ in range(RUNTIME.concurrency)
        ]
        while queue.queue._unfinished_tasks:
            ticks.append(1)
            await asyncio.sleep(0.005)
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    asyncio.run(run())
    db.expire_all()
    assert (
        maximum == 1
        and set(calls) == {d.id for d in cfg.traders[:6]}
        and len(ticks) >= 6
    )
    assert all(d.status == "OK" for d in db.scalars(select(JevDecision)))


def test_budget_sql_aggregates_include_empty_traders_and_queue_wait(db):
    from app.tournament_api import budget_stats

    cfg, t = make_tournament(db)
    f, _ = feature(db, t)
    queue = TournamentDecisionQueue(SimpleNamespace(cfg=cfg, decision_runtime=RUNTIME))
    queue.prepare(db, f, t, NOW)
    ds = list(db.scalars(select(JevDecision).order_by(JevDecision.id)))
    for d, status in zip(
        ds,
        [
            "OK",
            "LATE_DECISION",
            "BUDGET_EXPIRED",
            "QUEUE_FULL",
            "WORKER_ERROR",
            "QUEUED",
        ],
    ):
        d.status = status
    ds[0].decision_started_at = NOW + timedelta(seconds=1)
    ds[0].decision_completed_at = NOW + timedelta(seconds=6)
    ds[0].latency_ms = 5000
    db.flush()
    result = budget_stats(db, t)
    assert (
        result["jobs"] == 6
        and result["pending"] == 1
        and result["deadline_misses"] == 3
        and result["completed_before_deadline"] == 1
    )
    assert result["per_trader"]["jev-trend"]["average_queue_wait_ms"] == pytest.approx(
        1000, abs=0.1
    )
    assert result["per_trader"]["jev-trend"]["average_latency_ms"] == 5000

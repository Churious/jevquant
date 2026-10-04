"""Offline multi-trader replay using the same signals and paper execution engine."""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select, func

from .decision_runtime import DecisionRuntime
from .decision_queue import record_action
from .features import build_state, compact_state
from .jev import JevDecision
from .market import FeatureSnapshot, store_bar, aware
from .research import evaluation, state_hash, update_forward_returns
from .tournament import ensure_tournament, finish_if_due, leaderboard, start_tournament
from .traders import questions_for, runtime_for
from .trading import ResearchRun, process_execution_batch, snapshot_accounts


def decision_cache_key(symbol, timestamp, state, trader, questions, runtime):
    return (
        symbol,
        timestamp,
        state_hash(state),
        trader,
        state_hash(questions),
        runtime.provider,
        runtime.model,
    )


def baseline_cache_key(symbol, timestamp, state, trader):
    return (
        symbol,
        timestamp,
        state_hash(state),
        trader,
        state_hash({}),
        "baseline",
        "not-called",
    )


def replay_tournament(
    db, bars, cache, cfg, run_id, start=None, end=None, metadata=None, inherited=None
):
    if not cfg.tournament or not cfg.tournament.enabled:
        raise ValueError("Tournament replay requires enabled tournament configuration")
    if db.get(ResearchRun, run_id):
        raise ValueError("Run already exists; choose a new immutable experiment ID")
    if start and end and start >= end:
        raise ValueError("start must precede end")
    inherited = inherited or DecisionRuntime.from_env()
    tournament = ensure_tournament(db, run_id, cfg, inherited, mode="REPLAY")
    run = db.get(ResearchRun, run_id)
    run.metadata_json = {**run.metadata_json, **(metadata or {})}
    histories = defaultdict(lambda: defaultdict(list))
    events = defaultdict(list)
    for bar in bars:
        if bar.symbol in tournament.market_universe and (not end or bar.end <= end):
            events[bar.end].append(bar)
    last = None
    for timestamp, batch in sorted(events.items()):
        last = timestamp
        candles = {}
        for bar in batch:
            candles[(bar.symbol, bar.timeframe)] = store_bar(
                db, bar, "replay-import"
            ).id
            history = histories[bar.symbol][bar.timeframe]
            history.append(bar)
            del history[: -cfg.market.history_bars]
        if start and timestamp < start.replace(second=0, microsecond=0):
            continue
        states = {}
        for bar in batch:
            if bar.timeframe == cfg.strategy.timeframe:
                try:
                    states[bar.symbol] = build_state(
                        bar.symbol, bar.timeframe, histories[bar.symbol], timestamp, cfg
                    )
                    if cfg.tournament.state_precision_digits:
                        states[bar.symbol] = compact_state(
                            states[bar.symbol], cfg.tournament.state_precision_digits
                        )
                except ValueError:
                    pass
        if tournament.status == "PENDING" and states:
            start_tournament(db, tournament, start or timestamp, cfg)
        if tournament.status == "PENDING":
            continue
        execution = [
            b
            for b in batch
            if b.timeframe == cfg.market.execution_timeframe
            and b.timestamp >= aware(tournament.started_at)
            and b.end <= aware(tournament.ends_at)
        ]
        if tournament.status != "COMPLETED" and execution:
            process_execution_batch(db, run_id, execution, cfg)
        finish_if_due(db, tournament, timestamp, cfg)
        if tournament.status == "COMPLETED":
            update_forward_returns(db, run_id, timestamp, cfg)
            continue
        for symbol, state in states.items():
            feature = FeatureSnapshot(
                run_id=run_id,
                candle_id=candles[(symbol, cfg.strategy.timeframe)],
                timestamp=timestamp,
                state=state,
                config_hash=tournament.configuration_hash,
            )
            db.add(feature)
            db.flush()
            for definition in cfg.traders:
                if not definition.enabled:
                    continue
                decision = None
                available = max(timestamp, aware(tournament.started_at))
                if definition.type == "baseline":
                    cached_baseline = cache.get(
                        baseline_cache_key(symbol, timestamp, state, definition.id)
                    )
                    if cached_baseline:
                        from .replay import parse_time

                        available = parse_time(cached_baseline["observed_at"])
                if definition.type == "jev":
                    runtime, questions = (
                        runtime_for(definition, inherited),
                        questions_for(definition),
                    )
                    cached = cache.get(
                        decision_cache_key(
                            symbol, timestamp, state, definition.id, questions, runtime
                        )
                    )
                    if cached:
                        from .replay import parse_time

                        available = parse_time(cached["observed_at"])
                    status = "OK" if cached else "JEV_UNAVAILABLE"
                    deadline = min(
                        timestamp + timedelta(minutes=1), aware(tournament.ends_at)
                    )
                    if cached and cached.get("deadline_at"):
                        deadline = min(deadline, parse_time(cached["deadline_at"]))
                    if available >= deadline:
                        status = "LATE_DECISION"
                    decision = JevDecision(
                        feature_id=feature.id,
                        tournament_id=run_id,
                        trader_id=definition.id,
                        timestamp=available,
                        queued_at=parse_time(cached["queued_at"])
                        if cached and cached.get("queued_at")
                        else timestamp,
                        deadline_at=deadline,
                        decision_started_at=parse_time(cached["decision_started_at"])
                        if cached and cached.get("decision_started_at")
                        else timestamp,
                        decision_completed_at=available,
                        request={
                            "model": runtime.model,
                            "state": state,
                            "questions": questions,
                        },
                        raw_response=cached["raw_response"] if cached else None,
                        model_version=cached.get(
                            "model_version",
                            runtime.model_version(cached["raw_response"]["model"]),
                        )
                        if cached
                        else None,
                        attempts=[],
                        status=status,
                        latency_ms=max(
                            0, (available - timestamp).total_seconds() * 1000
                        ),
                        request_count=0,
                        estimated_cost=0,
                    )
                    db.add(decision)
                    db.flush()
                record_action(db, feature, definition, decision, cfg, available)
        db.flush()
        update_forward_returns(db, run_id, timestamp, cfg)
    if last and tournament.status not in {"PENDING", "COMPLETED"}:
        snapshot_accounts(db, run_id, last, cfg)
    db.flush()
    result = evaluation(db, run_id, cfg)
    result.update(
        tournament_id=run_id,
        tournament_status=tournament.status,
        final_report=tournament.final_report,
        ranking=leaderboard(db, tournament, cfg, last) if last else [],
        metadata=run.metadata_json,
        config_hash=tournament.configuration_hash,
        cached_decisions_used=db.scalar(
            select(func.count(JevDecision.id)).where(
                JevDecision.tournament_id == run_id, JevDecision.status == "OK"
            )
        ),
        cached_decisions_by_trader={
            d.id: db.scalar(
                select(func.count(JevDecision.id)).where(
                    JevDecision.tournament_id == run_id,
                    JevDecision.trader_id == d.id,
                    JevDecision.status == "OK",
                )
            )
            for d in cfg.traders
            if d.type == "jev" and d.enabled
        },
    )
    return result

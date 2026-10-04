import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .config import Config, load_config
from .decision_runtime import DecisionRuntime
from .db import Session, init_db
from .features import build_state
from .jev import JevDecision, JevResponse, QUESTIONS, StrategyDecision
from .market import Bar, FeatureSnapshot, aware, store_bar
from .research import evaluation, state_hash, update_forward_returns
from .trading import (
    STRATEGIES,
    ResearchRun,
    ensure_run,
    process_execution_batch,
    snapshot_accounts,
    strategy_action,
)


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamps must include UTC offset")
    return aware(result)


def load_bars(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        bars = [Bar.model_validate(row) for row in csv.DictReader(f)]
    keys = [(b.symbol, b.timeframe, b.timestamp) for b in bars]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate candle keys")
    return sorted(bars, key=lambda b: (b.end, b.symbol, b.timeframe))


def load_cache(path):
    cache = {}
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            key = (row["symbol"], parse_time(row["timestamp"]), row["state_hash"])
            if "question_hash" in row:
                if state_hash(row["questions"]) != row["question_hash"]:
                    raise ValueError("Cache question hash mismatch")
                key += (
                    row["trader_id"],
                    row["question_hash"],
                    row["provider"],
                    row["requested_model"],
                )
            if "observed_at" not in row or parse_time(row["observed_at"]) < key[1]:
                raise ValueError(
                    "cache must record answer availability as observed_at >= state timestamp"
                )
            if key in cache:
                raise ValueError("duplicate cached state")
            if row.get("kind") == "baseline":
                if (
                    row["provider"] != "baseline"
                    or row["requested_model"] != "not-called"
                    or row["questions"] != {}
                ):
                    raise ValueError("Invalid baseline availability cache")
            else:
                JevResponse.model_validate(
                    row["raw_response"],
                    context={"questions": row["questions"]}
                    if "question_hash" in row
                    else None,
                )
            cache[key] = row
    return cache


def replay(db, bars, cache, cfg, run_id, start=None, end=None, metadata=None):
    if db.get(ResearchRun, run_id):
        raise ValueError(
            "Run already exists; use a new run id. Existing experiments are immutable."
        )
    if start and end and start >= end:
        raise ValueError("start must precede end")
    config_hash = hashlib.sha256(cfg.model_dump_json().encode()).hexdigest()
    ensure_run(db, run_id, "REPLAY", cfg, config_hash, metadata)
    histories = defaultdict(lambda: defaultdict(list))
    events = defaultdict(list)
    for bar in bars:
        if not end or bar.end <= end:
            events[bar.end].append(bar)
    last = None
    active = False
    for timestamp, batch in sorted(events.items()):
        last = timestamp
        # Insert only bars available at this simulated time, even if DB contains future live bars.
        candle_ids = {}
        for bar in batch:
            candle = store_bar(db, bar, "replay-import")
            candle_ids[(bar.symbol, bar.timeframe)] = candle.id
            history = histories[bar.symbol][bar.timeframe]
            history.append(bar)
            if len(history) > cfg.market.history_bars:
                del history[: -cfg.market.history_bars]
        if start and timestamp <= start:
            continue
        states = {}
        for bar in batch:
            if bar.timeframe == cfg.strategy.timeframe:
                try:
                    states[bar.symbol] = build_state(
                        bar.symbol, bar.timeframe, histories[bar.symbol], timestamp, cfg
                    )
                except ValueError:
                    pass
        if not active and states:
            active = True
            snapshot_accounts(db, run_id, timestamp, cfg)
        if not active:
            continue
        execution = [
            b
            for b in batch
            if b.timeframe == cfg.market.execution_timeframe
            and (not start or b.timestamp >= start)
        ]
        if execution:
            process_execution_batch(db, run_id, execution, cfg)
        for bar in batch:
            if bar.timeframe != cfg.strategy.timeframe:
                continue
            state = states.get(bar.symbol)
            if not state:
                continue  # warmup or incomplete context; never substitute synthetic data
            feature = FeatureSnapshot(
                run_id=run_id,
                candle_id=candle_ids[(bar.symbol, bar.timeframe)],
                timestamp=timestamp,
                state=state,
                config_hash=config_hash,
            )
            db.add(feature)
            db.flush()
            cached = cache.get((bar.symbol, timestamp, state_hash(state)))
            available = (
                parse_time(cached["observed_at"])
                if cached and "observed_at" in cached
                else timestamp
            )
            decision = JevDecision(
                feature_id=feature.id,
                timestamp=available,
                request={
                    "state": state,
                    "questions": QUESTIONS,
                    "model": cached["raw_response"]["model"]
                    if cached
                    else "not-called",
                },
                raw_response=cached["raw_response"] if cached else None,
                attempts=[],
                model_version=cached.get(
                    "model_version", cached["raw_response"]["model"]
                )
                if cached
                else None,
                status="OK" if cached else "JEV_UNAVAILABLE",
                latency_ms=0,
                request_count=0,
                estimated_cost=0,
            )
            db.add(decision)
            db.flush()
            for name in STRATEGIES:
                action, reason = strategy_action(name, state, decision, cfg)
                db.add(
                    StrategyDecision(
                        run_id=run_id,
                        feature_id=feature.id,
                        jev_decision_id=decision.id if name == "jev" else None,
                        timestamp=available if name == "jev" else timestamp,
                        strategy=name,
                        strategy_version=cfg.strategy.version
                        if name == "jev"
                        else name + "-v1",
                        action=action,
                        reason=reason,
                    )
                )
        db.flush()
        update_forward_returns(db, run_id, timestamp, cfg)
    if last:
        snapshot_accounts(db, run_id, last, cfg)
    db.flush()
    result = evaluation(db, run_id, cfg)
    result["metadata"] = metadata or {}
    result["config_hash"] = config_hash
    result["cached_decisions_used"] = db.scalar(
        select(__import__("sqlalchemy").func.count(JevDecision.id))
        .join(FeatureSnapshot)
        .where(FeatureSnapshot.run_id == run_id, JevDecision.status == "OK")
    )
    result["note"] = (
        "Offline replay uses exact state-hash matches only. No cached Jev response means HOLD. Open positions are marked to market at end, not force-closed."
    )
    return result


def add_months(value, months):
    import calendar

    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    return value.replace(
        year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])
    )


def walk_forward(
    db,
    bars,
    cache,
    cfg,
    prefix,
    start,
    oos_start,
    oos_end,
    thresholds,
    trader_id=None,
    inherited=None,
):
    from datetime import timedelta

    if not start < oos_start < oos_end:
        raise ValueError("require development start < OOS start < OOS end")
    if not cache:
        raise ValueError("walk-forward needs real cached Jev judgments")
    if any(not 0 <= t <= 1 for t in thresholds):
        raise ValueError("thresholds must be probabilities")
    if trader_id and not any(
        d.id == trader_id and d.type == "jev" and d.enabled for d in cfg.traders
    ):
        raise ValueError("walk-forward requires an enabled Jev trader")
    target = trader_id or "jev"

    def candidate_config(threshold):
        candidate = cfg.model_copy(deep=True)
        if trader_id:
            next(
                d for d in candidate.traders if d.id == trader_id
            ).parameters.entry_probability = threshold
        else:
            candidate.strategy.long_threshold = candidate.strategy.short_threshold = (
                threshold
            )
        return candidate

    def simulate(candidate, identity, begin, stop, metadata):
        if not trader_id:
            return replay(db, bars, cache, candidate, identity, begin, stop, metadata)
        from .tournament_replay import replay_tournament
        import math

        candidate.tournament.duration_days = max(
            1, math.ceil((stop - begin).total_seconds() / 86400)
        )
        report = replay_tournament(
            db,
            bars,
            cache,
            candidate,
            identity,
            begin,
            stop,
            {**metadata, "target_trader": target},
            inherited,
        )
        report["cached_decisions_used"] = report["cached_decisions_by_trader"][target]
        return report

    if db.scalar(select(ResearchRun.id).where(ResearchRun.id.startswith(prefix + "-"))):
        raise ValueError(
            "experiment prefix already used; OOS evaluation is single-use per protocol"
        )
    folds = []
    train_start = start
    fold = 0
    selected = None
    embargo = timedelta(hours=cfg.research.embargo_hours)
    while True:
        train_end = add_months(train_start, cfg.research.train_months)
        validation_end = add_months(train_end, cfg.research.validation_months)
        if validation_end > oos_start - embargo:
            break
        candidates = []
        for i, t in enumerate(thresholds):
            candidate = candidate_config(t)
            report = simulate(
                candidate,
                f"{prefix}-fold{fold}-train{i}",
                train_start,
                train_end - embargo,
                {
                    "split": "Development",
                    "threshold": t,
                    "start": train_start.isoformat(),
                    "end": (train_end - embargo).isoformat(),
                },
            )
            if not report["cached_decisions_used"]:
                raise ValueError("no matched Jev observations in training window")
            candidates.append((report["portfolios"][target]["total_return"], t))
        selected = max(candidates, key=lambda x: (x[0], x[1]))[1]
        candidate = candidate_config(selected)
        validation = simulate(
            candidate,
            f"{prefix}-fold{fold}-validation",
            train_end,
            validation_end,
            {
                "split": "Validation",
                "frozen_threshold": selected,
                "start": train_end.isoformat(),
                "end": validation_end.isoformat(),
            },
        )
        folds.append(
            {
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "threshold": selected,
                "training_candidates": candidates,
                "validation": validation,
            }
        )
        train_start = add_months(train_start, cfg.research.validation_months)
        fold += 1
    if not folds:
        raise ValueError("insufficient development/validation windows with embargo")
    frozen = candidate_config(selected)
    oos = simulate(
        frozen,
        prefix + "-oos",
        oos_start,
        oos_end,
        {
            "split": "Out-of-sample Test",
            "frozen_threshold": selected,
            "start": oos_start.isoformat(),
            "end": oos_end.isoformat(),
            "selection": "Last rolling development window winner; validation and OOS never tune thresholds",
            "protocol_prefix": prefix,
        },
    )
    return {
        "folds": folds,
        "out_of_sample": oos,
        "embargo_hours": cfg.research.embargo_hours,
        "note": "One-shot OOS protocol is recorded in the database. Changing experiment ids does not make reused test data unseen.",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Paper-only reproducible offline research"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["replay", "walk-forward"]:
        p = sub.add_parser(command)
        p.add_argument("--candles", required=True)
        p.add_argument("--decisions")
        p.add_argument("--run-id", required=True)
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--output")
        p.add_argument("--tournament", action="store_true")
        p.add_argument(
            "--source-run",
            help="Reuse the source experiment's frozen configuration and runtime",
        )
        if command == "walk-forward":
            p.add_argument("--trader-id")
            p.add_argument("--oos-start", required=True)
            p.add_argument("--oos-end", required=True)
            p.add_argument("--thresholds", default="0.65,0.75,0.85")
    p = sub.add_parser("export-cache")
    p.add_argument("--run-id", default="live")
    p.add_argument("--output", required=True)
    args = parser.parse_args()
    cfg = load_config()
    init_db()
    with Session.begin() as db:
        if args.command == "export-cache":
            rows = []
            for d, f in db.execute(
                select(JevDecision, FeatureSnapshot)
                .join(FeatureSnapshot)
                .where(
                    FeatureSnapshot.run_id == args.run_id, JevDecision.status == "OK"
                )
            ):
                rows.append(
                    {
                        "symbol": f.state["symbol"],
                        "timestamp": aware(f.timestamp).isoformat(),
                        "state_hash": state_hash(f.state),
                        "raw_response": d.raw_response,
                        "model_version": d.model_version,
                        "observed_at": aware(d.timestamp).isoformat(),
                    }
                )
                if d.tournament_id:
                    from .trading import TraderRecord

                    record = db.scalar(
                        select(TraderRecord).where(
                            TraderRecord.tournament_id == d.tournament_id,
                            TraderRecord.trader_id == d.trader_id,
                        )
                    )
                    rows[-1].update(
                        trader_id=d.trader_id,
                        tournament_id=d.tournament_id,
                        questions=d.request["questions"],
                        question_hash=state_hash(d.request["questions"]),
                        provider=record.runtime["provider"],
                        requested_model=d.request["model"],
                        queued_at=aware(d.queued_at).isoformat()
                        if d.queued_at
                        else None,
                        deadline_at=aware(d.deadline_at).isoformat()
                        if d.deadline_at
                        else None,
                        decision_started_at=aware(d.decision_started_at).isoformat()
                        if d.decision_started_at
                        else None,
                    )
            for signal, feature in db.execute(
                select(StrategyDecision, FeatureSnapshot)
                .join(FeatureSnapshot)
                .where(
                    StrategyDecision.run_id == args.run_id,
                    StrategyDecision.tournament_id.is_not(None),
                    StrategyDecision.jev_decision_id.is_(None),
                )
            ):
                rows.append(
                    {
                        "kind": "baseline",
                        "symbol": feature.state["symbol"],
                        "timestamp": aware(feature.timestamp).isoformat(),
                        "state_hash": state_hash(feature.state),
                        "trader_id": signal.trader_id,
                        "questions": {},
                        "question_hash": state_hash({}),
                        "provider": "baseline",
                        "requested_model": "not-called",
                        "observed_at": aware(signal.timestamp).isoformat(),
                    }
                )
            Path(args.output).write_text(
                "\n".join(json.dumps(r, allow_nan=False) for r in rows),
                encoding="utf-8",
            )
            print(json.dumps({"exported": len(rows), "path": args.output}))
            return
        bars = load_bars(args.candles)
        cache = load_cache(args.decisions)
        inherited = DecisionRuntime.from_env()
        if args.source_run:
            source = db.get(ResearchRun, args.source_run)
            if not source:
                parser.error("Source run not found")
            cfg = Config.model_validate(source.config)
            identity = (source.metadata_json or {}).get("decision_runtime")
            if identity:
                inherited = DecisionRuntime(
                    **identity,
                    concurrency=1 if identity["provider"] == "local" else 4,
                    timeout_seconds=7 if identity["provider"] == "local" else None,
                )
            if args.tournament:
                from .trading import TraderRecord, Tournament

                source_tournament = db.get(Tournament, args.source_run)
                if source_tournament:
                    args.start = args.start or (
                        aware(source_tournament.started_at).isoformat()
                        if source_tournament.started_at
                        else None
                    )
                    args.end = args.end or (
                        aware(source_tournament.ends_at).isoformat()
                        if source_tournament.ends_at
                        else None
                    )

                for record in db.scalars(
                    select(TraderRecord).where(
                        TraderRecord.tournament_id == args.source_run
                    )
                ):
                    definition = next(
                        d for d in cfg.traders if d.id == record.trader_id
                    )
                    definition.questions, definition.policy = (
                        record.questions or None,
                        record.definition.get("policy"),
                    )
        elif not args.tournament:
            cfg.tournament, cfg.traders = None, []
        if args.command == "replay":
            simulate = replay
            extra = {}
            if args.tournament:
                from .tournament_replay import replay_tournament

                simulate, extra = replay_tournament, {"inherited": inherited}
            result = simulate(
                db,
                bars,
                cache,
                cfg,
                args.run_id,
                parse_time(args.start) if args.start else None,
                parse_time(args.end) if args.end else None,
                {
                    "source_file_sha256": hashlib.sha256(
                        Path(args.candles).read_bytes()
                    ).hexdigest(),
                    "split": "Research replay",
                    "source_run": args.source_run,
                },
                **extra,
            )
        else:
            if not args.start:
                parser.error("walk-forward requires --start")
            if args.tournament and not args.trader_id:
                parser.error("Tournament walk-forward requires --trader-id")
            result = walk_forward(
                db,
                bars,
                cache,
                cfg,
                args.run_id,
                parse_time(args.start),
                parse_time(args.oos_start),
                parse_time(args.oos_end),
                [float(t) for t in args.thresholds.split(",")],
                args.trader_id if args.tournament else None,
                inherited,
            )
        output = json.dumps(result, indent=2, allow_nan=False)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
        print(output)


if __name__ == "__main__":
    main()

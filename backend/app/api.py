from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from .db import Session, utcnow
from .jev import JevDecision, StrategyDecision
from .market import Candle, FeatureSnapshot, aware
from .runner import runner
from .trading import (
    Account,
    PaperOrder,
    PaperTrade,
    PortfolioSnapshot,
    Position,
    ResearchRun,
    equity,
    unrealized,
)
from .research import ForwardReturn, calibration, evaluation

router = APIRouter()


def serialize(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def get_account(db, strategy="jev", run_id=None):
    return db.scalar(
        select(Account).where(
            Account.run_id == (run_id or runner.run_id), Account.strategy == strategy
        )
    )


def system_status(account, run):
    if run and run.paused:
        return "PAUSED"
    if account and account.halted:
        return "RISK_HALTED"
    active = [runner.symbol_status.get(s) for s in runner.cfg.market.crypto_symbols]
    if active and all(s in {"DATA_ERROR", "DATA_STALE"} for s in active):
        return "DATA_ERROR"
    if any(s == "JEV_UNAVAILABLE" for s in active):
        return "JEV_ERROR"
    return runner.status


@router.get("/api/overview")
def overview():
    with Session() as db:
        a = get_account(db)
        run = db.get(ResearchRun, runner.run_id)
        positions = (
            list(db.scalars(select(Position).where(Position.account_id == a.id)))
            if a
            else []
        )
        value = equity(a, positions) if a else runner.cfg.trading.starting_capital
        trades = (
            list(db.scalars(select(PaperTrade).where(PaperTrade.account_id == a.id)))
            if a
            else []
        )
        snapshots = (
            list(
                db.scalars(
                    select(PortfolioSnapshot)
                    .where(PortfolioSnapshot.account_id == a.id)
                    .order_by(PortfolioSnapshot.timestamp)
                )
            )
            if a
            else []
        )
        peak = runner.cfg.trading.starting_capital
        drawdown = 0
        for s in snapshots:
            peak = max(peak, s.equity)
            drawdown = max(drawdown, 1 - s.equity / peak)
        decisions = list(
            db.scalars(
                select(JevDecision)
                .join(FeatureSnapshot)
                .where(FeatureSnapshot.run_id == runner.run_id)
            )
        )
        from zoneinfo import ZoneInfo

        today = utcnow().astimezone(ZoneInfo(runner.cfg.trading.timezone)).date()
        todays = [
            d
            for d in decisions
            if aware(d.timestamp)
            .astimezone(ZoneInfo(runner.cfg.trading.timezone))
            .date()
            == today
        ]
        calls = sum(d.request_count for d in decisions)
        priced = [d.estimated_cost for d in todays if d.request_count]
        day_start = a.day_start_equity if a and a.day == today.isoformat() else value
        return {
            "status": system_status(a, run),
            "paper_only": True,
            "mode": "LIVE_PAPER",
            "run_id": runner.run_id,
            "portfolio_value": value,
            "total_pnl": value - runner.cfg.trading.starting_capital,
            "today_pnl": value - day_start,
            "win_rate": sum(t.pnl > 0 for t in trades) / len(trades)
            if trades
            else None,
            "max_drawdown": drawdown,
            "open_positions": len(positions),
            "total_trades": len(trades),
            "jev_calls": calls,
            "jev_calls_today": sum(d.request_count for d in todays),
            "average_latency_ms": sum(d.latency_ms for d in todays if d.request_count)
            / sum(bool(d.request_count) for d in todays)
            if any(d.request_count for d in todays)
            else None,
            "jev_errors_today": sum(d.status != "OK" for d in todays),
            "estimated_api_cost": sum(priced)
            if priced and all(p is not None for p in priced)
            else None,
            "timestamp": utcnow(),
            "websocket": runner.ws_status,
            "day_timezone": runner.cfg.trading.timezone,
            "starting_capital": runner.cfg.trading.starting_capital,
            "decision_timeframe": runner.cfg.strategy.timeframe,
            "execution_timeframe": runner.cfg.market.execution_timeframe,
            "max_holding_minutes": runner.cfg.strategy.max_holding_minutes,
            "currency": runner.cfg.trading.base_currency,
            "config_hash": runner.config_hash,
            "decision_runtime": {
                "provider": runner.decision_runtime.provider,
                "model": runner.decision_runtime.model,
                "concurrency": runner.decision_runtime.concurrency,
            },
        }


@router.get("/api/market")
def market(trader_id: str | None = None):
    selected = trader_id or (
        next((t.id for t in runner.cfg.traders if t.enabled and t.type == "jev"), "jev")
        if runner.tournament_mode
        else "jev"
    )
    result = []
    with Session() as db:
        for symbol in (
            runner.cfg.market.crypto_symbols + runner.cfg.market.stock_symbols
        ):
            feature = db.scalar(
                select(FeatureSnapshot)
                .join(Candle)
                .where(FeatureSnapshot.run_id == runner.run_id, Candle.symbol == symbol)
                .order_by(FeatureSnapshot.timestamp.desc())
                .limit(1)
            )
            candle = db.scalar(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.timeframe == runner.cfg.market.execution_timeframe,
                )
                .order_by(Candle.timestamp.desc())
                .limit(1)
            )
            d = (
                db.scalar(
                    select(JevDecision).where(
                        JevDecision.feature_id == feature.id,
                        JevDecision.trader_id == selected,
                    )
                )
                if feature
                else None
            )
            s = (
                db.scalar(
                    select(StrategyDecision).where(
                        StrategyDecision.feature_id == feature.id,
                        StrategyDecision.strategy == selected,
                    )
                )
                if feature
                else None
            )
            answers = (
                (d.raw_response or {}).get("answers", {})
                if d and d.status == "OK"
                else {}
            )
            old = (
                db.scalar(
                    select(Candle)
                    .where(
                        Candle.symbol == symbol,
                        Candle.timeframe == "5m",
                        Candle.timestamp
                        <= candle.bar().end - timedelta(hours=24, minutes=5),
                    )
                    .order_by(Candle.timestamp.desc())
                    .limit(1)
                )
                if candle
                else None
            )
            state = feature.state if feature else {}
            result.append(
                {
                    "symbol": symbol,
                    "price": candle.close if candle else None,
                    "timestamp": candle.bar().end if candle else None,
                    "change_24h": candle.close / old.close - 1
                    if old
                    and 0
                    <= (
                        candle.bar().end - old.bar().end - timedelta(hours=24)
                    ).total_seconds()
                    < 300
                    else None,
                    "rsi": state.get("momentum", {}).get("rsi14"),
                    "trend": state.get("higher_timeframe", {}).get(
                        "trend_" + runner.cfg.strategy.timeframe
                    ),
                    "jev": answers,
                    "decision": s.action if s else "HOLD",
                    "status": runner.symbol_status.get(symbol, "WAITING"),
                    "regime": state.get("regime"),
                }
            )
    return result


@router.get("/api/equity")
def curves(run_id: str | None = None):
    with Session() as db:
        rows = list(
            db.execute(
                select(PortfolioSnapshot, Account.strategy)
                .join(Account)
                .where(Account.run_id == (run_id or runner.run_id))
                .order_by(PortfolioSnapshot.timestamp.desc())
                .limit(8000)
            )
        )
        points = defaultdict(dict)
        for snap, strategy in rows:
            points[aware(snap.timestamp).isoformat()][strategy] = snap.equity
        last = {
            s: runner.cfg.trading.starting_capital
            for s in ["buy_hold", "ema", "rsi", "jev"]
        }
        result = []
        for timestamp in sorted(points):
            last.update(points[timestamp])
            result.append({"timestamp": timestamp, **last})
        return result[:: max(1, len(result) // 600)]


@router.get("/api/positions")
def positions(strategy: str = "jev", run_id: str | None = None):
    with Session() as db:
        a = get_account(db, strategy, run_id)
        if not a:
            return []
        return [
            {
                **serialize(p),
                "pnl": unrealized(p) - p.entry_fee,
                "duration_minutes": (utcnow() - aware(p.timestamp)).total_seconds()
                / 60,
            }
            for p in db.scalars(select(Position).where(Position.account_id == a.id))
        ]


@router.get("/api/trades")
def trades(strategy: str = "jev", run_id: str | None = None):
    with Session() as db:
        a = get_account(db, strategy, run_id)
        if not a:
            return []
        result = []
        for trade in db.scalars(
            select(PaperTrade)
            .where(PaperTrade.account_id == a.id)
            .order_by(PaperTrade.closed_at.desc())
            .limit(200)
        ):
            order = db.get(PaperOrder, trade.entry_order_id)
            decision = db.get(StrategyDecision, order.decision_snapshot_id)
            jev = (
                db.get(JevDecision, decision.jev_decision_id)
                if decision.jev_decision_id
                else None
            )
            answer = (
                (jev.raw_response or {})
                .get("answers", {})
                .get("long_setup" if trade.side == "LONG" else "short_setup", {})
                if jev
                else {}
            )
            result.append(
                {
                    **serialize(trade),
                    "jev_confidence": answer.get("noul"),
                    "decision_id": jev.id if jev else None,
                }
            )
        return result


@router.get("/api/decisions")
def decisions(
    run_id: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    trader_id: str | None = None,
):
    with Session() as db:
        query = (
            select(JevDecision, FeatureSnapshot)
            .join(FeatureSnapshot)
            .where(FeatureSnapshot.run_id == (run_id or runner.run_id))
            .order_by(JevDecision.id.desc())
        )
        if trader_id:
            query = query.where(JevDecision.trader_id == trader_id)
        rows = db.execute(query.limit(limit))
        result = []
        for d, f in rows:
            s = db.scalar(
                select(StrategyDecision).where(
                    StrategyDecision.feature_id == f.id,
                    StrategyDecision.jev_decision_id == d.id,
                )
            )
            result.append(
                {
                    "id": d.id,
                    "timestamp": d.timestamp,
                    "symbol": f.state["symbol"],
                    "regime": f.state["regime"],
                    "status": d.status,
                    "model_version": d.model_version,
                    "trader_id": d.trader_id,
                    "tournament_id": d.tournament_id,
                    "decision_started_at": d.decision_started_at,
                    "decision_completed_at": d.decision_completed_at,
                    "decision_latency_ms": d.latency_ms,
                    "long_probability": (d.raw_response or {})
                    .get("answers", {})
                    .get("long_setup", {})
                    .get("noul")
                    if d.status == "OK"
                    else None,
                    "action": s.action if s else "HOLD",
                }
            )
        return result


@router.get("/api/decisions/{decision_id}")
def detail(decision_id: int):
    with Session() as db:
        d = db.get(JevDecision, decision_id)
        if not d:
            raise HTTPException(404, "Decision not found")
        f = db.get(FeatureSnapshot, d.feature_id)
        return {
            **serialize(d),
            "state": f.state,
            "config_hash": f.config_hash,
            "strategies": [
                serialize(s)
                for s in db.scalars(
                    select(StrategyDecision).where(StrategyDecision.feature_id == f.id)
                )
            ],
            "forward_returns": [
                serialize(r)
                for r in db.scalars(
                    select(ForwardReturn)
                    .where(ForwardReturn.decision_id == d.id)
                    .order_by(ForwardReturn.horizon_minutes)
                )
            ],
        }


@router.get("/api/calibration")
def calibration_api(
    run_id: str | None = None,
    horizon: int = 60,
    direction: str = "long",
    axis: str = "probability",
    regime: str | None = None,
    model: str | None = None,
    trader_id: str | None = None,
):
    if (
        horizon not in runner.cfg.research.forward_minutes
        or direction not in {"long", "short"}
        or axis not in {"probability", "confidence"}
    ):
        raise HTTPException(422, "Unsupported calibration dimension")
    with Session() as db:
        run = db.get(ResearchRun, run_id or runner.run_id)
        if not run:
            raise HTTPException(404, "Research run not found")
        from .config import Config

        return calibration(
            db,
            run_id or runner.run_id,
            Config.model_validate(run.config),
            horizon,
            direction,
            axis,
            regime,
            model,
            trader_id,
        )


@router.get("/api/evaluation")
def evaluation_api(run_id: str | None = None, trader_id: str | None = None):
    with Session() as db:
        run = db.get(ResearchRun, run_id or runner.run_id)
        if not run:
            raise HTTPException(404, "Research run not found")
        from .config import Config

        result = evaluation(
            db, run_id or runner.run_id, Config.model_validate(run.config), trader_id
        )
        result["runs"] = [
            {"id": r.id, "mode": r.mode, "metadata": r.metadata_json}
            for r in db.scalars(select(ResearchRun))
            if r.config.get("trading", {}).get("base_currency") == "KRW"
        ]
        result["active_run_id"] = runner.run_id
        result["config_hash"] = run.config_hash
        result["metadata"] = run.metadata_json
        return result


@router.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with Session() as db:
        counts = {
            "market_data_updates_total": db.scalar(select(func.count(Candle.id))),
            "jev_requests_total": db.scalar(select(func.sum(JevDecision.request_count)))
            or 0,
            "jev_errors_total": db.scalar(
                select(func.count(JevDecision.id)).where(JevDecision.status != "OK")
            ),
            "decisions_total": db.scalar(select(func.count(StrategyDecision.id))),
            "paper_orders_total": db.scalar(select(func.count(PaperOrder.id))),
        }
        a = get_account(db)
        ps = (
            list(db.scalars(select(Position).where(Position.account_id == a.id)))
            if a
            else []
        )
        counts.update(
            portfolio_value=equity(a, ps) if a else runner.cfg.trading.starting_capital,
            open_positions=len(ps),
        )
        return (
            "\n".join(
                f"# TYPE {k} {'gauge' if k in {'portfolio_value', 'open_positions'} else 'counter'}\n{k} {v}"
                for k, v in counts.items()
            )
            + "\n"
        )

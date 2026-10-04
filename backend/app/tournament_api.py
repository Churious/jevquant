from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func, case, and_

from .config import Config
from .db import Session, utcnow
from .jev import JevDecision, StrategyDecision
from .market import FeatureSnapshot, aware
from .runner import runner
from .tournament import comparison, leaderboard, records
from .trading import (
    PaperOrder,
    PaperTrade,
    PortfolioSnapshot,
    Position,
    ResearchRun,
    Tournament,
    TraderRecord,
    unrealized,
)

router = APIRouter()


def row_json(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def get_tournament(db, tournament_id=None):
    tournament = db.get(Tournament, tournament_id or runner.run_id)
    if not tournament:
        raise HTTPException(404, "Tournament not found")
    return tournament


def config_for(db, tournament):
    return Config.model_validate(db.get(ResearchRun, tournament.run_id).config)


def budget_stats(db, tournament):
    missed = ["BUDGET_EXPIRED", "LATE_DECISION", "QUEUE_FULL"]
    pending = ["QUEUED", "PROCESSING"]
    timed = and_(
        JevDecision.decision_started_at.is_not(None),
        JevDecision.decision_completed_at.is_not(None),
    )
    wait = (
        (
            func.julianday(JevDecision.decision_started_at)
            - func.julianday(JevDecision.queued_at)
        )
        * 86400000
        if db.get_bind().dialect.name == "sqlite"
        else func.extract(
            "epoch", JevDecision.decision_started_at - JevDecision.queued_at
        )
        * 1000
    )

    def count(condition):
        return func.sum(case((condition, 1), else_=0))

    aggregated = {
        row.trader_id: row
        for row in db.execute(
            select(
                JevDecision.trader_id,
                func.count().label("jobs"),
                count(JevDecision.status == "OK").label("ok"),
                count(JevDecision.status.in_(missed)).label("misses"),
                count(JevDecision.status.in_(pending)).label("pending"),
                count(JevDecision.status.not_in(["OK", *pending, *missed])).label(
                    "errors"
                ),
                func.avg(case((timed, JevDecision.latency_ms))).label("latency"),
                func.avg(case((timed, wait))).label("wait"),
            )
            .where(JevDecision.tournament_id == tournament.id)
            .group_by(JevDecision.trader_id)
        )
    }
    per_trader = {}
    for record in records(db, tournament.id):
        if record.kind != "jev":
            continue
        row = aggregated.get(record.trader_id)
        per_trader[record.trader_id] = {
            "jobs": row.jobs if row else 0,
            "ok": row.ok if row else 0,
            "deadline_misses": row.misses if row else 0,
            "errors": row.errors if row else 0,
            "average_latency_ms": float(row.latency)
            if row and row.latency is not None
            else None,
            "average_queue_wait_ms": float(row.wait)
            if row and row.wait is not None
            else None,
        }
    return {
        "jobs": sum(row.jobs for row in aggregated.values()),
        "pending": sum(row.pending for row in aggregated.values()),
        "concurrency": tournament.runtime.get(
            "concurrency", runner.decision_runtime.concurrency
        ),
        "deadline_misses": sum(row.misses for row in aggregated.values()),
        "completed_before_deadline": sum(row.ok for row in aggregated.values()),
        "per_trader": per_trader,
        "rule": "Queued calls must finish before min(next minute open, cycle budget, tournament end). Misses rotate across traders and assets; no retroactive fills.",
    }


def tournament_info(db, tournament):
    now = utcnow()
    cfg = config_for(db, tournament)
    end = aware(tournament.ends_at) if tournament.ends_at else None
    elapsed = (
        max(0, (min(now, end) - aware(tournament.started_at)).total_seconds())
        if tournament.started_at
        else 0
    )
    ranking = leaderboard(
        db, tournament, cfg, end if tournament.status == "COMPLETED" else now
    )
    return {
        **row_json(tournament),
        "remaining_seconds": max(0, (end - now).total_seconds()) if end else None,
        "day": min(tournament.duration_days, int(elapsed // 86400) + 1)
        if tournament.started_at
        else 0,
        "elapsed_seconds": elapsed,
        "starting_capital": 1000000,
        "participants": [row_json(r) for r in records(db, tournament.id)],
        "leaderboard": ranking,
        "comparison": comparison(ranking),
        "decision_budget": budget_stats(db, tournament),
        "paper_only": True,
        "market_status": runner.symbol_status,
    }


@router.get("/api/tournament/current")
def current():
    with Session() as db:
        return tournament_info(db, get_tournament(db))


@router.get("/api/tournament/{tournament_id}")
def tournament_detail(tournament_id: str):
    with Session() as db:
        return tournament_info(db, get_tournament(db, tournament_id))


@router.get("/api/tournament/{tournament_id}/leaderboard")
def ranking(tournament_id: str):
    with Session() as db:
        tournament = get_tournament(db, tournament_id)
        return leaderboard(db, tournament, config_for(db, tournament), utcnow())


@router.get("/api/tournament/{tournament_id}/equity")
def equity_curves(tournament_id: str):
    with Session() as db:
        tournament = get_tournament(db, tournament_id)
        points = defaultdict(dict)
        initial = {
            r.trader_id: r.definition["starting_capital"]
            for r in records(db, tournament_id)
        }
        for snapshot in db.scalars(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.tournament_id == tournament_id)
            .order_by(PortfolioSnapshot.timestamp)
        ):
            points[aware(snapshot.timestamp).isoformat()][snapshot.trader_id] = (
                snapshot.equity
            )
        last = dict(initial)
        result = []
        for timestamp in sorted(points):
            last.update(points[timestamp])
            result.append(
                {
                    "timestamp": timestamp,
                    "equity": dict(last),
                    "returns": {
                        key: value / initial[key] - 1 for key, value in last.items()
                    },
                }
            )
        step = max(1, len(result) // 600)
        sampled = result[::step]
        if result and sampled[-1] != result[-1]:
            sampled.append(result[-1])
        return sampled


def get_record(db, trader_id, tournament_id=None):
    tournament = get_tournament(db, tournament_id)
    record = db.scalar(
        select(TraderRecord).where(
            TraderRecord.tournament_id == tournament.id,
            TraderRecord.trader_id == trader_id,
        )
    )
    if not record:
        raise HTTPException(404, "Trader not found")
    return tournament, record


@router.get("/api/traders")
def participants(tournament_id: str | None = None):
    with Session() as db:
        tournament = get_tournament(db, tournament_id)
        return [row_json(r) for r in records(db, tournament.id)]


@router.get("/api/traders/{trader_id}")
def trader_detail(trader_id: str, tournament_id: str | None = None):
    with Session() as db:
        tournament, record = get_record(db, trader_id, tournament_id)
        summary = next(
            r
            for r in leaderboard(db, tournament, config_for(db, tournament), utcnow())
            if r["trader_id"] == trader_id
        )
        return {
            **row_json(record),
            "portfolio": summary,
            "tournament_status": tournament.status,
        }


@router.get("/api/traders/{trader_id}/portfolio")
def portfolio(trader_id: str, tournament_id: str | None = None):
    return trader_detail(trader_id, tournament_id)["portfolio"]


@router.get("/api/traders/{trader_id}/positions")
def positions(trader_id: str, tournament_id: str | None = None):
    with Session() as db:
        tournament, record = get_record(db, trader_id, tournament_id)
        return [
            {**row_json(p), "pnl": unrealized(p) - p.entry_fee}
            for p in db.scalars(
                select(Position).where(
                    Position.tournament_id == tournament.id,
                    Position.trader_id == trader_id,
                )
            )
        ]


@router.get("/api/traders/{trader_id}/trades")
def trades(trader_id: str, tournament_id: str | None = None):
    with Session() as db:
        tournament, record = get_record(db, trader_id, tournament_id)
        result = []
        for trade in db.scalars(
            select(PaperTrade)
            .where(
                PaperTrade.tournament_id == tournament.id,
                PaperTrade.trader_id == trader_id,
            )
            .order_by(PaperTrade.closed_at.desc())
            .limit(200)
        ):
            order = db.get(PaperOrder, trade.entry_order_id)
            result.append(
                {
                    **row_json(trade),
                    "strategy_decision_id": order.decision_snapshot_id,
                    "model_version": order.jev_model_version,
                }
            )
        return result


@router.get("/api/traders/{trader_id}/decisions")
def decisions(
    trader_id: str,
    tournament_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    with Session() as db:
        tournament, record = get_record(db, trader_id, tournament_id)
        result = []
        for signal, feature in db.execute(
            select(StrategyDecision, FeatureSnapshot)
            .join(FeatureSnapshot)
            .where(
                StrategyDecision.run_id == tournament.run_id,
                StrategyDecision.strategy == trader_id,
            )
            .order_by(StrategyDecision.timestamp.desc())
            .limit(limit)
        ):
            decision = (
                db.get(JevDecision, signal.jev_decision_id)
                if signal.jev_decision_id
                else None
            )
            raw = (decision.raw_response or {}).get("answers", {}) if decision else {}
            quality = (record.definition.get("policy") or {}).get("quality")
            if not quality and record.kind == "jev":
                from .traders import policy_for
                from .config import TraderDefinition

                quality = policy_for(
                    TraderDefinition.model_validate(record.definition)
                )["quality"]
            result.append(
                {
                    **row_json(signal),
                    "symbol": feature.state["symbol"],
                    "decision_id": decision.id if decision else None,
                    "model_version": decision.model_version if decision else None,
                    "model_status": decision.status if decision else None,
                    "confidence": raw.get(quality, {}).get("confidence"),
                    "decision_latency_ms": decision.latency_ms if decision else None,
                    "decision_started_at": decision.decision_started_at
                    if decision
                    else None,
                    "decision_completed_at": decision.decision_completed_at
                    if decision
                    else None,
                }
            )
        return result

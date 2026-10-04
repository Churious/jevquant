"""Seven-day tournaments over the existing isolated paper account ledger."""

from datetime import datetime, timedelta
from statistics import mean, median

from sqlalchemy import select

from .market import Candle, aware, MINUTES
from .research import state_hash, trading_metrics
from .traders import (
    definition_fingerprint,
    effective_config,
    questions_for,
    runtime_for,
    policy_for,
)
from .trading import (
    Account,
    PaperOrder,
    PaperTrade,
    PortfolioSnapshot,
    Position,
    ResearchRun,
    Tournament,
    TraderRecord,
    ensure_run,
    equity,
    snapshot_accounts,
    unrealized,
)


def market_universe(cfg):
    mode = cfg.tournament.market_mode
    return (cfg.market.crypto_symbols if mode in {"crypto", "mixed"} else []) + (
        cfg.market.stock_symbols + cfg.market.us_stock_symbols
        if mode in {"stock", "mixed"}
        else []
    )


def tournament_fingerprint(cfg, inherited):
    configuration = cfg.model_dump()
    # Preserve the fingerprints of existing experiments when new optional features are unused.
    for section, defaults in {
        "market": {"us_stock_symbols": [], "kr_holidays": [], "us_holidays": []},
        "trading": {
            "usd_krw": None,
            "fx_reference": None,
            "allow_us_fractional": False,
        },
        "tournament": {
            "scheduled_start_at": None,
            "scheduled_end_at": None,
            "start_market": "any",
        },
    }.items():
        for key, value in defaults.items():
            if configuration.get(section) and configuration[section].get(key) == value:
                configuration[section].pop(key, None)
    return state_hash(
        {
            "configuration": configuration,
            "runtime": inherited.identity(),
            "concurrency": inherited.concurrency,
            "timeout": inherited.timeout_seconds,
            "participants": {
                d.id: definition_fingerprint(d, runtime_for(d, inherited))
                for d in cfg.traders
                if d.enabled
            },
        }
    )


def ensure_tournament(db, tournament_id, cfg, inherited, mode="LIVE_PAPER"):
    fingerprint = tournament_fingerprint(cfg, inherited)
    existing = db.get(Tournament, tournament_id)
    if existing:
        if existing.configuration_hash != fingerprint:
            raise ValueError(
                "TOURNAMENT_CONFIG_CHANGED: frozen participants/model/questions/rules require a new LAB_TOURNAMENT_ID"
            )
        return existing
    if db.get(ResearchRun, tournament_id):
        raise ValueError("Tournament ID is already used by a legacy experiment")
    definitions = [d for d in cfg.traders if d.enabled]
    ensure_run(
        db,
        tournament_id,
        mode,
        cfg,
        fingerprint,
        {"tournament": True, "decision_runtime": inherited.identity()},
        participants=[d.id for d in definitions],
    )
    tournament = Tournament(
        id=tournament_id,
        run_id=tournament_id,
        name=cfg.tournament.name,
        duration_days=cfg.tournament.duration_days,
        status="PENDING",
        market_universe=market_universe(cfg),
        runtime={
            **inherited.identity(),
            "concurrency": inherited.concurrency,
            "timeout_seconds": inherited.timeout_seconds,
        },
        configuration_hash=fingerprint,
    )
    db.add(tournament)
    db.flush()
    for definition in definitions:
        runtime = runtime_for(definition, inherited)
        record = TraderRecord(
            tournament_id=tournament_id,
            trader_id=definition.id,
            name=definition.name,
            kind=definition.type,
            definition={
                **definition.model_dump(),
                "questions": questions_for(definition)
                if definition.type == "jev"
                else None,
                "policy": policy_for(definition) if definition.type == "jev" else None,
            },
            configuration=effective_config(cfg, definition).model_dump(),
            questions=questions_for(definition),
            runtime=runtime.identity() if definition.type == "jev" else None,
            configuration_hash=definition_fingerprint(definition, runtime),
        )
        db.add(record)
        db.flush()
        account = db.scalar(
            select(Account).where(
                Account.run_id == tournament_id, Account.strategy == definition.id
            )
        )
        account.tournament_id, account.trader_id, account.trader_record_id = (
            tournament_id,
            definition.id,
            record.id,
        )
        account.strategy_style = definition.strategy
    db.flush()
    return tournament


def start_tournament(db, tournament, timestamp, cfg):
    if tournament.status != "PENDING":
        return
    from .equity_sessions import window_status

    if window_status(cfg, timestamp):
        return
    tournament.started_at = aware(timestamp)
    tournament.ends_at = (
        datetime.fromisoformat(cfg.tournament.scheduled_end_at)
        if cfg.tournament.scheduled_end_at
        else aware(timestamp) + timedelta(days=tournament.duration_days)
    )
    tournament.status = "RUNNING"
    snapshot_accounts(db, tournament.run_id, timestamp, cfg)
    db.flush()


def records(db, tournament_id):
    return list(
        db.scalars(
            select(TraderRecord)
            .where(TraderRecord.tournament_id == tournament_id)
            .order_by(TraderRecord.id)
        )
    )


def exposure_metrics(snapshots, asof):
    seconds, weighted, invested = 0.0, 0.0, 0.0
    max_exposure = 0.0
    for i, snap in enumerate(snapshots):
        stop = (
            aware(snapshots[i + 1].timestamp) if i + 1 < len(snapshots) else aware(asof)
        )
        dt = max(0, (min(stop, aware(asof)) - aware(snap.timestamp)).total_seconds())
        fraction = (snap.exposure_value or 0) / snap.equity if snap.equity > 0 else 0
        seconds += dt
        weighted += fraction * dt
        invested += dt if (snap.exposure_value or 0) > 0 else 0
        max_exposure = max(max_exposure, fraction)
    return {
        "time_in_market_seconds": invested,
        "time_in_market": invested / seconds if seconds else 0,
        "average_exposure": weighted / seconds if seconds else 0,
        "max_exposure": max_exposure,
    }


def leaderboard(db, tournament, cfg, asof):
    if tournament.final_report:
        return tournament.final_report["ranking"]
    result = []
    for record in records(db, tournament.id):
        account = db.scalar(
            select(Account).where(Account.trader_record_id == record.id)
        )
        positions = list(
            db.scalars(select(Position).where(Position.account_id == account.id))
        )
        snaps = list(
            db.scalars(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.account_id == account.id)
                .order_by(PortfolioSnapshot.timestamp)
            )
        )
        trades = list(
            db.scalars(select(PaperTrade).where(PaperTrade.account_id == account.id))
        )
        orders = list(
            db.scalars(select(PaperOrder).where(PaperOrder.account_id == account.id))
        )
        initial = record.definition["starting_capital"]
        value = equity(account, positions)
        metrics = trading_metrics(snaps, trades, initial, cfg.trading.timezone)
        from zoneinfo import ZoneInfo

        today = (
            aware(asof).astimezone(ZoneInfo(cfg.trading.timezone)).date().isoformat()
        )
        day_start = account.day_start_equity if account.day == today else value
        exposure = sum(p.current_price * p.quantity for p in positions)
        result.append(
            {
                "trader_id": record.trader_id,
                "name": record.name,
                "type": record.kind,
                "provider": (record.runtime or {}).get("provider"),
                "model": (record.runtime or {}).get("model"),
                "starting_capital": initial,
                "current_equity": value,
                "cash": account.cash,
                "net_pnl": value - initial,
                "return_pct": value / initial - 1,
                "today_pnl": value - day_start,
                "realized_pnl": sum(t.pnl for t in trades),
                "unrealized_pnl": sum(unrealized(p) - p.entry_fee for p in positions),
                "max_drawdown": metrics["max_drawdown"],
                "win_rate": metrics["win_rate"],
                "profit_factor": metrics["profit_factor"],
                "trades": len(trades),
                "current_exposure": exposure / value if value > 0 else 0,
                "crypto_exposure": sum(
                    p.current_price * p.quantity for p in positions if "/" in p.symbol
                ),
                "stock_exposure": sum(
                    p.current_price * p.quantity
                    for p in positions
                    if "/" not in p.symbol
                ),
                "fees_paid": sum(o.fees for o in orders),
                "turnover": sum(o.fill_price * o.quantity for o in orders),
                "risk_halted": account.halted,
                "open_positions": len(positions),
                **exposure_metrics(snaps, asof),
                "risk_adjusted_score": (value / initial - 1) / metrics["max_drawdown"]
                if metrics["max_drawdown"] > 0
                else None,
            }
        )
    result.sort(key=lambda r: (-r["current_equity"], r["trader_id"]))
    previous = None
    for i, row in enumerate(result):
        row["rank"] = (
            i + 1
            if previous is None
            or abs(row["current_equity"] - previous["current_equity"]) > 0.005
            else previous["rank"]
        )
        previous = row
    return result


def comparison(ranking):
    jevs = [r for r in ranking if r["type"] == "jev"]
    baselines = [r for r in ranking if r["type"] == "baseline"]
    best, baseline = jevs[0] if jevs else None, baselines[0] if baselines else None
    return {
        "best_jev": best,
        "worst_jev": jevs[-1] if jevs else None,
        "best_baseline": baseline,
        "jev_vs_best_baseline_pp": (best["return_pct"] - baseline["return_pct"]) * 100
        if best and baseline
        else None,
        "average_jev_return": mean(r["return_pct"] for r in jevs) if jevs else None,
        "median_jev_return": median(r["return_pct"] for r in jevs) if jevs else None,
    }


def finish_if_due(db, tournament, now, cfg):
    if (
        tournament.status == "PENDING"
        and cfg.tournament.scheduled_end_at
        and aware(now) >= datetime.fromisoformat(cfg.tournament.scheduled_end_at)
    ):
        tournament.status = "EXPIRED"
        db.get(ResearchRun, tournament.run_id).paused = True
        return True
    if (
        tournament.status == "COMPLETED"
        or not tournament.ends_at
        or aware(now) < aware(tournament.ends_at)
    ):
        return False
    end = aware(tournament.ends_at)
    prices = {}
    for symbol in tournament.market_universe:
        candle = db.scalar(
            select(Candle)
            .where(
                Candle.symbol == symbol,
                Candle.timeframe == cfg.market.execution_timeframe,
                Candle.timestamp
                <= end - timedelta(minutes=MINUTES[cfg.market.execution_timeframe]),
            )
            .order_by(Candle.timestamp.desc())
            .limit(1)
        )
        if candle:
            from .equity_sessions import price_factor

            prices[symbol] = {
                "price": candle.close * price_factor(symbol, cfg),
                "quote_price": candle.close,
                "account_currency": cfg.trading.base_currency,
                "observed_at": candle.bar().end.isoformat(),
                "stale": (end - candle.bar().end).total_seconds() > 120,
            }
    for account in db.scalars(
        select(Account).where(Account.tournament_id == tournament.id)
    ):
        for position in db.scalars(
            select(Position).where(Position.account_id == account.id)
        ):
            if position.symbol in prices:
                position.current_price = prices[position.symbol]["price"]
    snapshot_accounts(db, tournament.run_id, end, cfg)
    db.flush()
    ranking = leaderboard(db, tournament, cfg, end)
    winners = [r for r in ranking if r["rank"] == 1]
    report = {
        "tournament_id": tournament.id,
        "valuation": "mark_to_market",
        "started_at": aware(tournament.started_at).isoformat(),
        "ends_at": end.isoformat(),
        "configuration_hash": tournament.configuration_hash,
        "winner": winners[0] if len(winners) == 1 else None,
        "joint_winners": winners,
        "ranking": ranking,
        **comparison(ranking),
        "valuation_prices": prices,
        "fees_by_trader": {r["trader_id"]: r["fees_paid"] for r in ranking},
        "risk_adjusted_ranking": sorted(
            ranking,
            key=lambda r: (
                r["risk_adjusted_score"] is None,
                -(r["risk_adjusted_score"] or 0),
                r["trader_id"],
            ),
        ),
        "risk_adjusted_definition": "Unannualized tournament return / maximum drawdown; undefined at zero drawdown. Seven days do not support a 30-day Sharpe estimate.",
        "note": "Open positions are frozen at the last available confirmed price on/before ends_at. No liquidation fees are imputed; stale/missing quotes are disclosed.",
    }
    tournament.final_report = report
    tournament.status = "COMPLETED"
    db.get(ResearchRun, tournament.run_id).paused = True
    from .jev import StrategyDecision

    for decision in db.scalars(
        select(StrategyDecision).where(
            StrategyDecision.run_id == tournament.run_id,
            StrategyDecision.status == "PENDING",
        )
    ):
        decision.status = "TOURNAMENT_ENDED"
    for snap in db.scalars(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.tournament_id == tournament.id,
            PortfolioSnapshot.timestamp == end,
        )
    ):
        snap.status = "COMPLETED"
    db.flush()
    return True

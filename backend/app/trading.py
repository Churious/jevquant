from abc import ABC, abstractmethod
from datetime import timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, ExperimentScope, json_type
from .jev import JevResponse, StrategyDecision
from .market import aware, MINUTES

STRATEGIES = ["buy_hold", "ema", "rsi", "jev"]


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(json_type)
    config_hash: Mapped[str] = mapped_column(String(64))
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(json_type, default=dict)


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_days: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    market_universe: Mapped[list] = mapped_column(json_type)
    runtime: Mapped[dict] = mapped_column(json_type)
    configuration_hash: Mapped[str] = mapped_column(String(64))
    final_report: Mapped[dict | None] = mapped_column(json_type, nullable=True)


class TraderRecord(Base):
    __tablename__ = "traders"
    __table_args__ = (UniqueConstraint("tournament_id", "trader_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[str] = mapped_column(ForeignKey("tournaments.id"), index=True)
    trader_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(16))
    definition: Mapped[dict] = mapped_column(json_type)
    configuration: Mapped[dict] = mapped_column(json_type)
    questions: Mapped[dict] = mapped_column(json_type)
    runtime: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    configuration_hash: Mapped[str] = mapped_column(String(64))


class Account(Base, ExperimentScope):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("run_id", "strategy"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    strategy: Mapped[str] = mapped_column(String(32))
    cash: Mapped[float] = mapped_column(Float)
    day: Mapped[str] = mapped_column(String(10), default="")
    day_start_equity: Mapped[float] = mapped_column(Float)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    trader_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("traders.id"), nullable=True
    )
    strategy_style: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Position(Base, ExperimentScope):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    entry_fee: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"))


class PaperOrder(Base, ExperimentScope):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    requested_price: Mapped[float] = mapped_column(Float)
    fill_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float)
    slippage: Mapped[float] = mapped_column(Float)
    strategy_version: Mapped[str] = mapped_column(String(80))
    jev_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_decisions.id")
    )
    reason: Mapped[str] = mapped_column(String(160))


class PaperTrade(Base, ExperimentScope):
    __tablename__ = "paper_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    pnl_percent: Mapped[float] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float)
    entry_order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"))
    exit_order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"))
    reason: Mapped[str] = mapped_column(String(160))


class PortfolioSnapshot(Base, ExperimentScope):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "timestamp"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    open_positions: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    exposure_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    crypto_exposure: Mapped[float | None] = mapped_column(Float, nullable=True)
    stock_exposure: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketCursor(Base):
    __tablename__ = "market_cursors"
    __table_args__ = (UniqueConstraint("run_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))


def ensure_run(db, run_id, mode, cfg, config_hash, metadata=None, participants=None):
    run = db.get(ResearchRun, run_id)
    if run and run.config_hash != config_hash:
        raise ValueError("CONFIG_CHANGED: use a new run id for a new experiment")
    if not run:
        run = ResearchRun(
            id=run_id,
            mode=mode,
            config=cfg.model_dump(),
            config_hash=config_hash,
            metadata_json=metadata or {},
        )
        db.add(run)
        db.flush()
    for strategy in STRATEGIES if participants is None else participants:
        account = db.scalar(
            select(Account).where(
                Account.run_id == run_id, Account.strategy == strategy
            )
        )
        if not account:
            db.add(
                Account(
                    run_id=run_id,
                    strategy=strategy,
                    cash=cfg.trading.starting_capital,
                    day_start_equity=cfg.trading.starting_capital,
                )
            )
    db.flush()
    return run


def account_config(db, account, cfg):
    if account.trader_record_id:
        from .config import Config

        return Config.model_validate(
            db.get(TraderRecord, account.trader_record_id).configuration
        )
    return cfg


def passive_account(account):
    return account.strategy_style == "buy_hold" or account.strategy in {
        "buy_hold",
        "baseline-buyhold",
    }


def strategy_action(name, state, response, cfg):
    if name == "buy_hold":
        return "LONG", "BUY_AND_HOLD"
    if name == "ema":
        return (
            ("LONG", "EMA9_ABOVE_EMA21")
            if state["trend"]["ema9_above_21"]
            else ("EXIT", "EMA9_BELOW_EMA21")
        )
    if name == "rsi":
        rsi = state["momentum"]["rsi14"]
        if rsi <= cfg.strategy.rsi_entry:
            return "LONG", "RSI_OVERSOLD"
        if rsi >= cfg.strategy.rsi_exit:
            return "EXIT", "RSI_EXIT"
        return "HOLD", "RSI_NO_SIGNAL"
    if not response or response.status != "OK":
        return "HOLD", "JEV_UNAVAILABLE"
    answers = JevResponse.model_validate(response.raw_response).answers
    if answers["avoid_trade"].noul >= cfg.strategy.avoid_threshold:
        return "HOLD", "AVOID_THRESHOLD"
    if answers["reversal_risk"].score > cfg.strategy.max_reversal_score:
        return "HOLD", "REVERSAL_RISK"
    if answers["setup_quality"].score < cfg.strategy.min_setup_score:
        return "HOLD", "SETUP_QUALITY"
    long = (
        answers["long_setup"].noul >= cfg.strategy.long_threshold
        and state["higher_timeframe"]["trend_1h"] != "strong_down"
    )
    short = (
        answers["short_setup"].noul >= cfg.strategy.short_threshold
        and state["higher_timeframe"]["trend_1h"] != "strong_up"
    )
    if long == short:
        return "HOLD", "CONFLICTING_SIGNALS" if long else "BELOW_THRESHOLD"
    return ("LONG", "JEV_LONG_SETUP") if long else ("SHORT", "JEV_SHORT_SETUP")


def unrealized(position, price=None):
    return (
        (1 if position.side == "LONG" else -1)
        * ((price or position.current_price) - position.entry_price)
        * position.quantity
    )


def equity(account, positions):
    return account.cash + sum(
        p.entry_price * p.quantity + unrealized(p) for p in positions
    )


def update_daily(account, positions, timestamp, cfg):
    value = equity(account, positions)
    from zoneinfo import ZoneInfo

    day = aware(timestamp).astimezone(ZoneInfo(cfg.trading.timezone)).date().isoformat()
    if account.day != day:
        account.day, account.day_start_equity, account.halted = day, value, False
    if value <= account.day_start_equity * (1 - cfg.trading.daily_loss_limit):
        account.halted = True
    return account.halted


def position_size(value, cash, fill_price, atr, cfg):
    distance = atr * cfg.risk.atr_stop_multiplier
    if value <= 0 or cash <= 0 or distance <= 0 or distance >= fill_price:
        return 0, distance
    # Include expected round-trip fees and exit slippage in the loss budget.
    per_unit_risk = (
        distance
        + 2 * fill_price * cfg.simulation.fee_rate
        + fill_price * cfg.simulation.slippage_bps / 10000
    )
    quantity = min(
        value * cfg.trading.risk_per_trade / per_unit_risk,
        value * cfg.trading.max_position_allocation / fill_price,
        cash / (fill_price * (1 + cfg.simulation.fee_rate)),
    )
    return max(0, quantity), distance


class Broker(ABC):
    @abstractmethod
    def order(
        self, db, account, decision, timestamp, symbol, side, price, quantity, reason
    ): ...


class PaperBroker(Broker):
    def __init__(self, cfg):
        self.cfg = cfg

    def fill(self, price, side):
        return price * (
            1
            + (1 if side in {"BUY", "COVER"} else -1)
            * self.cfg.simulation.slippage_bps
            / 10000
        )

    def order(
        self, db, account, decision, timestamp, symbol, side, price, quantity, reason
    ):
        from .jev import JevDecision

        if side not in {"BUY", "SELL", "SHORT", "COVER"} or quantity <= 0:
            raise ValueError("invalid simulated order")
        if account.tournament_id and (
            decision.tournament_id != account.tournament_id
            or decision.trader_id != account.trader_id
        ):
            raise ValueError(
                "Decision and account must belong to the same tournament/trader"
            )
        fill = self.fill(price, side)
        jev = (
            db.get(JevDecision, decision.jev_decision_id)
            if decision.jev_decision_id
            else None
        )
        order = PaperOrder(
            tournament_id=account.tournament_id,
            trader_id=account.trader_id,
            account_id=account.id,
            timestamp=timestamp,
            symbol=symbol,
            side=side,
            requested_price=price,
            fill_price=fill,
            quantity=quantity,
            fees=fill * quantity * self.cfg.simulation.fee_rate,
            slippage=(fill - price) * quantity,
            strategy_version=decision.strategy_version,
            jev_model_version=jev.model_version if jev else None,
            decision_snapshot_id=decision.id,
            reason=reason,
        )
        db.add(order)
        db.flush()
        return order

    def open(self, db, account, positions, decision, bar, state):
        if account.tournament_id:
            tournament = db.get(Tournament, account.tournament_id)
            if tournament.status != "RUNNING" or bar.timestamp >= aware(
                tournament.ends_at
            ):
                return (
                    "TOURNAMENT_ENDED"
                    if tournament.status == "COMPLETED"
                    or tournament.ends_at
                    and bar.timestamp >= aware(tournament.ends_at)
                    else "PAUSED"
                )
        if account.halted or len(positions) >= self.cfg.trading.max_positions:
            return "RISK_HALTED" if account.halted else "MAX_POSITIONS"
        side = "BUY" if decision.action == "LONG" else "SHORT"
        fill = self.fill(bar.open, side)
        qty, distance = position_size(
            equity(account, positions),
            account.cash,
            fill,
            state["volatility"]["atr14"],
            self.cfg,
        )
        if not passive_account(account):
            target_return = distance * self.cfg.risk.reward_risk_ratio / fill
            round_trip_cost = 2 * (
                self.cfg.simulation.fee_rate + self.cfg.simulation.slippage_bps / 10000
            )
            if (
                target_return
                <= round_trip_cost + self.cfg.simulation.min_net_target_return
            ):
                return "COST_FILTER"
        if "/" not in bar.symbol:
            import math

            if decision.action == "SHORT" and not self.cfg.trading.allow_stock_shorts:
                return "STOCK_SHORT_DISABLED"
            qty = math.floor(qty)  # Korean equities and ETFs trade in whole shares.
        if qty <= 0:
            return "INVALID_SIZE"
        order = self.order(
            db,
            account,
            decision,
            bar.timestamp,
            bar.symbol,
            side,
            bar.open,
            qty,
            decision.reason,
        )
        account.cash -= order.fill_price * qty + order.fees
        direction = 1 if side == "BUY" else -1
        # Buy & Hold is a capped passive allocation, deliberately without exit barriers.
        passive = passive_account(account)
        position = Position(
            tournament_id=account.tournament_id,
            trader_id=account.trader_id,
            account_id=account.id,
            symbol=bar.symbol,
            side=decision.action,
            timestamp=bar.timestamp,
            entry_price=order.fill_price,
            current_price=bar.open,
            quantity=qty,
            entry_fee=order.fees,
            stop_loss=None if passive else fill - direction * distance,
            take_profit=None
            if passive
            else fill + direction * distance * self.cfg.risk.reward_risk_ratio,
            entry_order_id=order.id,
        )
        db.add(position)
        db.flush()
        positions.append(position)
        return "EXECUTED"

    def close(self, db, account, position, decision, timestamp, price, reason):
        order = self.order(
            db,
            account,
            decision,
            timestamp,
            position.symbol,
            "SELL" if position.side == "LONG" else "COVER",
            price,
            position.quantity,
            reason,
        )
        gross = unrealized(position, order.fill_price)
        account.cash += position.entry_price * position.quantity + gross - order.fees
        net = gross - position.entry_fee - order.fees
        db.add(
            PaperTrade(
                tournament_id=account.tournament_id,
                trader_id=account.trader_id,
                account_id=account.id,
                symbol=position.symbol,
                side=position.side,
                opened_at=position.timestamp,
                closed_at=timestamp,
                entry_price=position.entry_price,
                exit_price=order.fill_price,
                quantity=position.quantity,
                pnl=net,
                pnl_percent=net / (position.entry_price * position.quantity),
                fees=position.entry_fee + order.fees,
                entry_order_id=position.entry_order_id,
                exit_order_id=order.id,
                reason=reason,
            )
        )
        db.delete(position)

    def barrier(self, position, bar):
        if position.stop_loss is None:
            return None
        long = position.side == "LONG"
        stop = bar.low <= position.stop_loss if long else bar.high >= position.stop_loss
        take = (
            bar.high >= position.take_profit
            if long
            else bar.low <= position.take_profit
        )
        # Unknown intrabar path: stop wins if both touched. Gap stops fill at adverse open.
        if stop:
            return "STOP_LOSS", min(bar.open, position.stop_loss) if long else max(
                bar.open, position.stop_loss
            )
        if take:
            return "TAKE_PROFIT", position.take_profit
        return None


def process_execution_bar(db, run_id, bar, cfg, phase="all", allow_entries=True):
    run = db.get(ResearchRun, run_id)
    tournament = db.get(Tournament, run_id)
    if tournament and (
        tournament.status in {"PENDING", "COMPLETED"}
        or bar.timestamp < aware(tournament.started_at)
        or bar.end > aware(tournament.ends_at)
    ):
        return
    for account in db.scalars(
        select(Account).where(Account.run_id == run_id).order_by(Account.id)
    ):
        cfg_for_account = account_config(db, account, cfg)
        broker = PaperBroker(cfg_for_account)
        positions = list(
            db.scalars(select(Position).where(Position.account_id == account.id))
        )
        update_daily(account, positions, bar.timestamp, cfg_for_account)
        timed_exit = False
        session_exit = False
        if phase != "close" and not passive_account(account):
            from zoneinfo import ZoneInfo

            local = aware(bar.timestamp).astimezone(ZoneInfo(cfg.trading.timezone))
            session_exit = (
                cfg_for_account.strategy.max_holding_minutes > 0
                and "/" not in bar.symbol
                and local.hour * 60 + local.minute >= 920
            )
            for p in list(positions):
                if p.symbol != bar.symbol:
                    continue
                time_exit = (
                    cfg_for_account.strategy.max_holding_minutes > 0
                    and (bar.timestamp - aware(p.timestamp)).total_seconds()
                    >= cfg_for_account.strategy.max_holding_minutes * 60
                )
                if time_exit or session_exit:
                    entry = db.get(PaperOrder, p.entry_order_id)
                    decision = db.get(StrategyDecision, entry.decision_snapshot_id)
                    broker.close(
                        db,
                        account,
                        p,
                        decision,
                        bar.timestamp,
                        bar.open,
                        "SESSION_EXIT" if session_exit else "TIME_EXIT",
                    )
                    positions.remove(p)
                    timed_exit = True
        for p in positions:
            if p.symbol == bar.symbol:
                p.current_price = bar.open
        update_daily(account, positions, bar.timestamp, cfg_for_account)
        pending = (
            list(
                db.scalars(
                    select(StrategyDecision)
                    .where(
                        StrategyDecision.run_id == run_id,
                        StrategyDecision.strategy == account.strategy,
                        StrategyDecision.status == "PENDING",
                        StrategyDecision.timestamp <= bar.timestamp,
                    )
                    .order_by(StrategyDecision.id)
                )
            )
            if phase != "close"
            else []
        )
        for decision in pending:
            from .market import FeatureSnapshot

            feature = db.get(FeatureSnapshot, decision.feature_id)
            if feature.state["symbol"] != bar.symbol:
                continue
            age = (bar.timestamp - aware(decision.timestamp)).total_seconds()
            if (
                age
                > timedelta(
                    minutes=MINUTES[cfg.strategy.timeframe]
                    * cfg.market.stale_multiplier
                ).total_seconds()
            ):
                decision.status = "EXPIRED"
                continue
            p = next((p for p in positions if p.symbol == bar.symbol), None)
            if decision.action == "HOLD":
                decision.status = "HOLD"
            elif p and (decision.action == "EXIT" or decision.action != p.side):
                broker.close(
                    db, account, p, decision, bar.timestamp, bar.open, decision.reason
                )
                positions.remove(p)
                decision.status = "EXECUTED"
            elif p or decision.action == "EXIT":
                decision.status = "NO_CHANGE"
            elif timed_exit or session_exit:
                decision.status = "COOLDOWN"
            elif (
                run.paused
                or not allow_entries
                or account.tournament_id
                and (
                    db.get(Tournament, account.tournament_id).status != "RUNNING"
                    or bar.timestamp
                    >= aware(db.get(Tournament, account.tournament_id).ends_at)
                )
            ):
                decision.status = "PAUSED"
            else:
                decision.status = broker.open(
                    db, account, positions, decision, bar, feature.state
                )
        for p in list(positions) if phase != "open" else []:
            if p.symbol != bar.symbol:
                continue
            hit = broker.barrier(p, bar)
            if hit:
                entry = db.get(PaperOrder, p.entry_order_id)
                decision = db.get(StrategyDecision, entry.decision_snapshot_id)
                # Candle-level barrier time is its end; actual intrabar time is unknown.
                broker.close(db, account, p, decision, bar.end, hit[1], hit[0])
                positions.remove(p)
            else:
                p.current_price = bar.close
        update_daily(
            account,
            positions,
            bar.end if phase != "open" else bar.timestamp,
            cfg_for_account,
        )
    db.flush()


def process_execution_batch(db, run_id, bars, cfg, allow_entries=True):
    tournament = db.get(Tournament, run_id)
    if tournament:
        if tournament.status in {"PENDING", "COMPLETED"}:
            return
        bars = [
            b
            for b in bars
            if b.timestamp >= aware(tournament.started_at)
            and b.end <= aware(tournament.ends_at)
        ]
    if not bars:
        return
    # All signals are filled at the batch's open before any symbol's future high/low/close is inspected.
    accounts = list(db.scalars(select(Account).where(Account.run_id == run_id)))
    for a in accounts:
        ps = list(db.scalars(select(Position).where(Position.account_id == a.id)))
        update_daily(a, ps, bars[0].timestamp, account_config(db, a, cfg))
        opens = {b.symbol: b.open for b in bars}
        for p in ps:
            if p.symbol in opens:
                p.current_price = opens[p.symbol]
    for bar in sorted(bars, key=lambda b: b.symbol):
        process_execution_bar(
            db, run_id, bar, cfg, phase="open", allow_entries=allow_entries
        )
    for bar in sorted(bars, key=lambda b: b.symbol):
        process_execution_bar(
            db, run_id, bar, cfg, phase="close", allow_entries=allow_entries
        )
    snapshot_accounts(db, run_id, bars[0].end, cfg)


def snapshot_accounts(db, run_id, timestamp, cfg):
    tournament = db.get(Tournament, run_id)
    if tournament and tournament.status == "COMPLETED":
        return
    paused = db.get(ResearchRun, run_id).paused
    for account in db.scalars(select(Account).where(Account.run_id == run_id)):
        positions = list(
            db.scalars(select(Position).where(Position.account_id == account.id))
        )
        if not db.scalar(
            select(PortfolioSnapshot.id).where(
                PortfolioSnapshot.account_id == account.id,
                PortfolioSnapshot.timestamp == timestamp,
            )
        ):
            db.add(
                PortfolioSnapshot(
                    tournament_id=account.tournament_id,
                    trader_id=account.trader_id,
                    account_id=account.id,
                    timestamp=timestamp,
                    equity=equity(account, positions),
                    cash=account.cash,
                    open_positions=len(positions),
                    exposure_value=sum(p.current_price * p.quantity for p in positions),
                    crypto_exposure=sum(
                        p.current_price * p.quantity
                        for p in positions
                        if "/" in p.symbol
                    ),
                    stock_exposure=sum(
                        p.current_price * p.quantity
                        for p in positions
                        if "/" not in p.symbol
                    ),
                    status="RISK_HALTED"
                    if account.halted
                    else "PAUSED"
                    if paused
                    else "RUNNING",
                )
            )

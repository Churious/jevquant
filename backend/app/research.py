import hashlib
import json
import math
from collections import defaultdict
from datetime import timedelta

import numpy as np
from sqlalchemy import DateTime, Float, ForeignKey, Integer, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .jev import JevDecision, StrategyDecision
from .market import Candle, FeatureSnapshot, MINUTES, aware
from .trading import Account, PaperOrder, PaperTrade, PortfolioSnapshot


def state_hash(state):
    return hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class ForwardReturn(Base):
    __tablename__ = "forward_returns"
    __table_args__ = (UniqueConstraint("decision_id", "horizon_minutes"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("jev_decisions.id"), index=True)
    horizon_minutes: Mapped[int] = mapped_column(Integer)
    origin_timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    origin_price: Mapped[float] = mapped_column(Float)
    target_timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    candle_id: Mapped[int] = mapped_column(ForeignKey("candles.id"))
    return_value: Mapped[float] = mapped_column(Float)


def update_forward_returns(db, run_id, now, cfg):
    # The decision must already exist; no labeling before the answer was available.
    execution_tf = cfg.market.execution_timeframe
    resolution = MINUTES[execution_tf]
    oldest = aware(now) - timedelta(minutes=max(cfg.research.forward_minutes) + 60)
    rows = db.execute(
        select(JevDecision, FeatureSnapshot)
        .join(FeatureSnapshot)
        .where(
            FeatureSnapshot.run_id == run_id,
            JevDecision.timestamp <= now,
            JevDecision.timestamp >= oldest,
        )
    )
    for decision, feature in rows:
        origin = aware(decision.timestamp)
        symbol = feature.state["symbol"]
        reference = db.scalar(
            select(Candle)
            .where(
                Candle.symbol == symbol,
                Candle.timeframe == execution_tf,
                Candle.timestamp <= origin - timedelta(minutes=resolution),
            )
            .order_by(Candle.timestamp.desc())
            .limit(1)
        )
        if (
            not reference
            or (origin - reference.bar().end).total_seconds() > resolution * 60
        ):
            continue
        completed = set(
            db.scalars(
                select(ForwardReturn.horizon_minutes).where(
                    ForwardReturn.decision_id == decision.id
                )
            )
        )
        for minutes in cfg.research.forward_minutes:
            target = origin + timedelta(minutes=minutes)
            if minutes in completed or target > aware(now):
                continue
            candle = db.scalar(
                select(Candle)
                .where(
                    Candle.symbol == symbol,
                    Candle.timeframe == execution_tf,
                    Candle.timestamp >= target - timedelta(minutes=resolution),
                    Candle.timestamp <= now - timedelta(minutes=resolution),
                )
                .order_by(Candle.timestamp)
                .limit(1)
            )
            if (
                not candle
                or (candle.bar().end - target).total_seconds() > resolution * 60
            ):
                continue
            db.add(
                ForwardReturn(
                    decision_id=decision.id,
                    horizon_minutes=minutes,
                    origin_timestamp=origin,
                    origin_price=reference.close,
                    target_timestamp=target,
                    timestamp=candle.bar().end,
                    candle_id=candle.id,
                    return_value=candle.close / reference.close - 1,
                )
            )


def stats(values):
    values = np.asarray(values, dtype=float)
    n = len(values)
    return {
        "sample_count": n,
        "mean_return": float(np.mean(values)) if n else None,
        "median_return": float(np.median(values)) if n else None,
        "win_rate": float(np.mean(values > 0)) if n else None,
        "standard_deviation": float(np.std(values, ddof=1)) if n > 1 else None,
    }


def calibration(
    db,
    run_id,
    cfg,
    horizon=60,
    direction="long",
    axis="probability",
    regime=None,
    model=None,
):
    samples = []
    for d, f, r in db.execute(
        select(JevDecision, FeatureSnapshot, ForwardReturn)
        .join(FeatureSnapshot)
        .join(ForwardReturn, ForwardReturn.decision_id == JevDecision.id)
        .where(
            FeatureSnapshot.run_id == run_id,
            JevDecision.status == "OK",
            ForwardReturn.horizon_minutes == horizon,
        )
    ):
        if regime and f.state["regime"] != regime or model and d.model_version != model:
            continue
        answers = d.raw_response["answers"]
        probability = (
            answers[f"{direction}_setup"]["noul"]
            if axis == "probability"
            else answers["setup_quality"]["confidence"]
        )
        samples.append(
            {
                "probability": probability,
                "return": r.return_value * (1 if direction == "long" else -1),
                "day": aware(r.origin_timestamp).date().isoformat(),
                "regime": f.state["regime"],
                "model": d.model_version,
            }
        )
    edges = cfg.research.bucket_edges
    buckets = []
    for i, (low, high) in enumerate(zip(edges, edges[1:])):
        members = [
            s
            for s in samples
            if low <= s["probability"] < high
            or (i == len(edges) - 2 and s["probability"] == high)
        ]
        buckets.append(
            {
                "low": low,
                "high": high,
                **stats([s["return"] for s in members]),
                "mean_probability": float(np.mean([s["probability"] for s in members]))
                if members
                else None,
            }
        )
    p = np.array([s["probability"] for s in samples])
    r = np.array([s["return"] for s in samples])
    correlation = (
        float(np.corrcoef(p, r)[0, 1])
        if len(p) > 2 and np.std(p) > 0 and np.std(r) > 0
        else None
    )
    # Daily blocks retain within-day correlation. Require multiple independent days.
    by_day = defaultdict(list)
    for s in samples:
        by_day[s["day"]].append(s["return"])
    ci = None
    if len(by_day) >= 10:
        rng = np.random.default_rng(2026)
        days = list(by_day.values())
        means = [
            np.mean(
                [v for idx in rng.integers(0, len(days), len(days)) for v in days[idx]]
            )
            for _ in range(1000)
        ]
        ci = [float(x) for x in np.quantile(means, [0.025, 0.975])]
    return {
        "horizon_minutes": horizon,
        "direction": direction,
        "axis": axis,
        "buckets": buckets,
        "total_samples": len(samples),
        "below_bucket_range": sum(s["probability"] < edges[0] for s in samples),
        "correlation": correlation,
        "mean_return_daily_block_ci95": ci,
        "observed_days": len(by_day),
        "brier_direction_proxy": float(np.mean((p - (r > 0)) ** 2)) if len(p) else None,
        "models": sorted(set(s["model"] for s in samples)),
        "note": "구간별 연관성을 관찰하는 통계이며 수익성의 증명이 아닙니다. 셋업 확률은 상승 수익률의 확률과 다릅니다. 겹치는 관찰 기간의 표본은 독립적이지 않습니다.",
    }


def trading_metrics(snapshots, trades, initial, trading_timezone="UTC"):
    if not snapshots:
        return {
            "total_return": 0,
            "annualized_return": None,
            "sharpe_ratio": None,
            "sortino_ratio": None,
            "max_drawdown": 0,
            "profit_factor": None,
            "win_rate": None,
            "average_win": None,
            "average_loss": None,
            "expectancy": None,
            "trade_count": 0,
        }
    value = snapshots[-1].equity
    total = value / initial - 1
    # Daily equity series, rather than incorrectly annualizing irregular event ticks.
    from zoneinfo import ZoneInfo

    days = {}
    for s in snapshots:
        days[
            aware(s.timestamp).astimezone(ZoneInfo(trading_timezone)).date().isoformat()
        ] = s.equity
    daily = np.array(list(days.values()))
    returns = (
        np.diff(daily) / daily[:-1]
        if len(daily) > 1 and np.all(daily[:-1] > 0)
        else np.array([])
    )
    peak = initial
    dd = 0
    for s in snapshots:
        peak = max(peak, s.equity)
        dd = max(dd, 1 - s.equity / peak)
    elapsed = (
        aware(snapshots[-1].timestamp) - aware(snapshots[0].timestamp)
    ).total_seconds() / 86400
    annual = None
    if elapsed >= 30 and value > 0:
        exponent = math.log(value / initial) * 365 / elapsed
        annual = math.expm1(exponent) if abs(exponent) < 700 else None
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0
    downside = (
        float(np.sqrt(np.mean(np.minimum(returns, 0) ** 2))) if len(returns) else 0
    )
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]
    return {
        "total_return": total,
        "annualized_return": annual,
        "sharpe_ratio": float(np.mean(returns) / sd * np.sqrt(365))
        if len(returns) >= 30 and sd > 0
        else None,
        "sortino_ratio": float(np.mean(returns) / downside * np.sqrt(365))
        if len(returns) >= 30 and downside > 0
        else None,
        "max_drawdown": dd,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "win_rate": len(wins) / len(trades) if trades else None,
        "average_win": float(np.mean(wins)) if wins else None,
        "average_loss": float(np.mean(losses)) if losses else None,
        "expectancy": float(np.mean([t.pnl for t in trades])) if trades else None,
        "trade_count": len(trades),
        "observed_days": len(days),
        "ending_equity": value,
    }


def evaluation(db, run_id, cfg):
    portfolios = {}
    for a in db.scalars(select(Account).where(Account.run_id == run_id)):
        snapshots = list(
            db.scalars(
                select(PortfolioSnapshot)
                .where(PortfolioSnapshot.account_id == a.id)
                .order_by(PortfolioSnapshot.timestamp)
            )
        )
        trades = list(
            db.scalars(select(PaperTrade).where(PaperTrade.account_id == a.id))
        )
        portfolios[a.strategy] = trading_metrics(
            snapshots, trades, cfg.trading.starting_capital, cfg.trading.timezone
        )
    ds = list(
        db.scalars(
            select(StrategyDecision).where(
                StrategyDecision.run_id == run_id, StrategyDecision.strategy == "jev"
            )
        )
    )
    evaluated = []
    candidate_horizon = 5 if cfg.strategy.timeframe == "1m" else 60
    for d, f, r in db.execute(
        select(JevDecision, FeatureSnapshot, ForwardReturn)
        .join(FeatureSnapshot)
        .join(ForwardReturn, ForwardReturn.decision_id == JevDecision.id)
        .where(
            FeatureSnapshot.run_id == run_id,
            JevDecision.status == "OK",
            ForwardReturn.horizon_minutes == candidate_horizon,
        )
    ):
        s = db.scalar(
            select(StrategyDecision).where(
                StrategyDecision.feature_id == f.id, StrategyDecision.strategy == "jev"
            )
        )
        if s:
            evaluated.append((s.action, r.return_value, f.state["regime"]))
    positives = [
        ret * (1 if action == "LONG" else -1)
        for action, ret, _ in evaluated
        if action in {"LONG", "SHORT"}
    ]
    regimes = {}
    for regime in [
        "TRENDING_UP",
        "TRENDING_DOWN",
        "RANGING",
        "HIGH_VOLATILITY",
        "LOW_VOLATILITY",
    ]:
        regime_trades = []
        for trade, order, s, f in db.execute(
            select(PaperTrade, PaperOrder, StrategyDecision, FeatureSnapshot)
            .join(PaperOrder, PaperTrade.entry_order_id == PaperOrder.id)
            .join(
                StrategyDecision, PaperOrder.decision_snapshot_id == StrategyDecision.id
            )
            .join(FeatureSnapshot, StrategyDecision.feature_id == FeatureSnapshot.id)
            .where(
                StrategyDecision.run_id == run_id, StrategyDecision.strategy == "jev"
            )
        ):
            if f.state["regime"] == regime:
                regime_trades.append(trade)
        regimes[regime] = {
            "trade_count": len(regime_trades),
            "net_pnl": sum(t.pnl for t in regime_trades),
            "win_rate": sum(t.pnl > 0 for t in regime_trades) / len(regime_trades)
            if regime_trades
            else None,
        }
    return {
        "run_id": run_id,
        "portfolios": portfolios,
        "jev": {
            "decision_count": len(ds),
            "decision_frequency": sum(d.action in {"LONG", "SHORT"} for d in ds)
            / len(ds)
            if ds
            else None,
            "abstention_rate": sum(d.action == "HOLD" for d in ds) / len(ds)
            if ds
            else None,
            "false_positive_rate": sum(v <= 0 for v in positives) / len(positives)
            if positives
            else None,
            "false_positive_horizon_minutes": candidate_horizon,
            "false_positive_definition": f"{candidate_horizon}분 방향성 수익률이 0 이하인 후보의 비율입니다. 리스크 검증에서 거절된 후보도 포함합니다.",
            "regimes": regimes,
        },
        "limitations": "샤프·소르티노 지수는 일간 수익률 30개 이상, 연환산은 30일 이상일 때 계산합니다. 무위험 수익률은 0이며 연간 365일 기준입니다. 겹치는 관측만으로 통계적 유의성을 주장하지 않습니다.",
    }

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import load_config
from app.features import build_state, compact_state
from app.jev import JevDecision
from app.market import Bar
from app.replay import load_cache, walk_forward
from app.research import state_hash
from app.tournament_replay import (
    decision_cache_key,
    replay_tournament,
    baseline_cache_key,
)
from test_tournament import NOW, RUNTIME, raw_response
from app.traders import questions_for


def bars_fixture(cfg):
    histories = {}
    bars = []
    # Only eight active minutes after a sufficient shared warm-up.
    for tf, minutes in [("1m", 1), ("5m", 5), ("15m", 15), ("1h", 60)]:
        bars_for_tf = []
        for i in range(65):
            when = NOW - timedelta(minutes=(65 - i) * minutes)
            b = Bar(
                symbol="BTC/KRW",
                timeframe=tf,
                timestamp=when,
                open=100 + i * 0.01,
                high=101 + i * 0.01,
                low=99 + i * 0.01,
                close=100 + i * 0.01,
                volume=10,
            )
            bars_for_tf.append(b)
            bars.append(b)
        histories[tf] = bars_for_tf
    for i in range(8):
        bars.append(
            Bar(
                symbol="BTC/KRW",
                timeframe="1m",
                timestamp=NOW + timedelta(minutes=i),
                open=100.64,
                high=100.8,
                low=100.5,
                close=100.64,
                volume=10,
            )
        )
    state = build_state("BTC/KRW", "1m", histories, NOW, cfg)
    state = compact_state(state, cfg.tournament.state_precision_digits)
    return bars, state


def test_multi_trader_replay_cache_partition_availability_and_reproducibility(
    db, tmp_path
):
    cfg = load_config()
    bars, state = bars_fixture(cfg)
    cache = {}
    rows = []
    for i, d in enumerate(cfg.traders[:6]):
        q = questions_for(d)
        row = {
            "symbol": "BTC/KRW",
            "timestamp": NOW.isoformat(),
            "state_hash": state_hash(state),
            "trader_id": d.id,
            "questions": q,
            "question_hash": state_hash(q),
            "provider": "local",
            "requested_model": RUNTIME.model,
            "raw_response": raw_response(d),
            "model_version": "local:tev1:0.8b",
            "observed_at": (NOW + timedelta(seconds=3 + i)).isoformat(),
        }
        rows.append(row)
        cache[decision_cache_key("BTC/KRW", NOW, state, d.id, q, RUNTIME)] = row
    path = tmp_path / "cache.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    assert load_cache(path) == cache
    one = replay_tournament(
        db,
        bars,
        cache,
        cfg,
        "multi-one",
        NOW,
        NOW + timedelta(minutes=8),
        inherited=RUNTIME,
    )
    two = replay_tournament(
        db,
        list(reversed(bars)),
        cache,
        cfg,
        "multi-two",
        NOW,
        NOW + timedelta(minutes=8),
        inherited=RUNTIME,
    )
    assert one["portfolios"] == two["portfolios"]
    assert one["cached_decisions_used"] == 6
    assert all(n == 1 for n in one["cached_decisions_by_trader"].values())
    assert len(one["portfolios"]) == 9
    ds = list(
        db.scalars(
            select(JevDecision).where(
                JevDecision.tournament_id == "multi-one", JevDecision.status == "OK"
            )
        )
    )
    assert len({d.feature_id for d in ds}) == 1 and len({d.trader_id for d in ds}) == 6
    with pytest.raises(ValueError, match="already exists"):
        replay_tournament(db, bars, cache, cfg, "multi-one", inherited=RUNTIME)


def test_tournament_walkforward_selects_only_target_development(db, monkeypatch):
    import app.tournament_replay as module

    calls = []

    def simulate(db, bars, cache, cfg, identity, begin, end, metadata, inherited):
        trend = next(
            d for d in cfg.traders if d.id == "jev-trend"
        ).parameters.entry_probability
        momentum = next(
            d for d in cfg.traders if d.id == "jev-momentum"
        ).parameters.entry_probability
        calls.append((metadata["split"], trend, momentum))
        # A different trader's large profit must not influence trend selection.
        return {
            "cached_decisions_used": 2,
            "cached_decisions_by_trader": {"jev-trend": 1},
            "portfolios": {
                "jev-trend": {"total_return": 1 if trend == 0.85 else 0},
                "jev-momentum": {"total_return": 100 if trend == 0.65 else 0},
            },
        }

    monkeypatch.setattr(module, "replay_tournament", simulate)
    cfg = load_config()
    result = walk_forward(
        db,
        [],
        {"cache": True},
        cfg,
        "new-wf",
        NOW,
        NOW + timedelta(days=180),
        NOW + timedelta(days=210),
        [0.65, 0.75, 0.85],
        "jev-trend",
        RUNTIME,
    )
    assert all(f["threshold"] == 0.85 for f in result["folds"])
    assert all(p == 0.75 for _, _, p in calls)
    assert all(t == 0.85 for split, t, _ in calls if split != "Development")


def test_replay_preserves_baseline_observation_lag_and_fractional_start(db, tmp_path):
    from app.trading import Account, PaperOrder

    cfg = load_config()
    bars, state = bars_fixture(cfg)
    row = {
        "kind": "baseline",
        "symbol": "BTC/KRW",
        "timestamp": NOW.isoformat(),
        "state_hash": state_hash(state),
        "trader_id": "baseline-buyhold",
        "questions": {},
        "question_hash": state_hash({}),
        "provider": "baseline",
        "requested_model": "not-called",
        "observed_at": (NOW + timedelta(seconds=3)).isoformat(),
    }
    path = tmp_path / "baseline.jsonl"
    path.write_text(json.dumps(row))
    cache = load_cache(path)
    assert baseline_cache_key("BTC/KRW", NOW, state, "baseline-buyhold") in cache
    replay_tournament(
        db,
        bars,
        cache,
        cfg,
        "delayed-baseline",
        NOW,
        NOW + timedelta(minutes=1),
        inherited=RUNTIME,
    )
    assert not db.scalar(
        select(PaperOrder)
        .join(Account)
        .where(
            Account.run_id == "delayed-baseline", Account.strategy == "baseline-buyhold"
        )
    )
    definition = cfg.traders[0]
    questions = questions_for(definition)
    cache[
        decision_cache_key("BTC/KRW", NOW, state, definition.id, questions, RUNTIME)
    ] = {
        "observed_at": (NOW + timedelta(seconds=10)).isoformat(),
        "raw_response": raw_response(definition),
    }
    replay_tournament(
        db,
        bars,
        cache,
        cfg,
        "fractional-start",
        NOW + timedelta(seconds=5),
        NOW + timedelta(minutes=1),
        inherited=RUNTIME,
    )
    assert (
        db.scalar(
            select(JevDecision).where(
                JevDecision.tournament_id == "fractional-start",
                JevDecision.status == "OK",
            )
        ).trader_id
        == "jev-trend"
    )

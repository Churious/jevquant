from datetime import datetime, timezone

import pytest

from app.config import load_config
from app.replay import walk_forward
from app.trading import ResearchRun


def test_threshold_selection_never_uses_validation_or_oos(db, monkeypatch):
    import app.replay as module

    calls = []

    def run(db, bars, cache, cfg, run_id, start, end, metadata):
        calls.append(
            (run_id, start, end, cfg.strategy.long_threshold, metadata["split"])
        )
        # Validation would prefer a different threshold, but it must not affect selection.
        result = 1 if cfg.strategy.long_threshold == 0.75 else 0
        return {
            "cached_decisions_used": 1,
            "portfolios": {"jev": {"total_return": result}},
            "run_id": run_id,
        }

    monkeypatch.setattr(module, "replay", run)
    cfg = load_config()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    oos = datetime(2026, 7, 1, tzinfo=timezone.utc)
    result = walk_forward(
        db,
        [],
        {"cached": "fixture"},
        cfg,
        "protocol-test",
        start,
        oos,
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        [0.65, 0.75, 0.85],
    )
    assert result["folds"]
    assert all(row[2] <= oos for row in calls if row[4] != "Out-of-sample Test")
    assert [r for r in calls if r[4] == "Out-of-sample Test"][0][3] == 0.75
    for fold in result["folds"]:
        assert fold["threshold"] == 0.75
    assert calls[-1][4] == "Out-of-sample Test"


def test_oos_protocol_reuse_rejected(db):
    cfg = load_config()
    db.add(
        ResearchRun(
            id="locked-oos",
            mode="REPLAY",
            config=cfg.model_dump(),
            config_hash="x",
            metadata_json={},
        )
    )
    db.flush()
    with pytest.raises(ValueError, match="single-use"):
        walk_forward(
            db,
            [],
            {"cache": True},
            cfg,
            "locked",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            [0.75],
        )

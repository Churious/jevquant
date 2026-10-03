from app.trading import Position, PaperBroker, process_execution_bar, ResearchRun
from app.market import FeatureSnapshot
from sqlalchemy import select
from test_trading import setup


def test_pause_blocks_new_entries(db):
    cfg, account, bar, decision = setup(db)
    db.get(ResearchRun, "test").paused = True
    db.get(FeatureSnapshot, decision.feature_id).state = {
        "symbol": bar.symbol,
        "volatility": {"atr14": 2},
    }
    process_execution_bar(db, "test", bar, cfg)
    assert decision.status == "PAUSED"
    assert not list(db.scalars(select(Position)))


def test_pause_keeps_stop_monitoring(db):
    cfg, account, bar, decision = setup(db)
    broker = PaperBroker(cfg)
    positions = []
    broker.open(db, account, positions, decision, bar, {"volatility": {"atr14": 2}})
    decision.status = "EXECUTED"
    db.get(ResearchRun, "test").paused = True
    nextbar = bar.model_copy(
        update={"timestamp": bar.end, "open": 90, "low": 89, "high": 91, "close": 90}
    )
    process_execution_bar(db, "test", nextbar, cfg)
    assert not list(db.scalars(select(Position)))

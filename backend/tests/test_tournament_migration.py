import json

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db import Base
from app.migrations import migrate
from app.trading import Account
from app.jev import JevDecision
from app.config import load_config
from test_jev import response


def test_sqlite_migration_preserves_legacy_ids_balances_raw_and_foreign_keys():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    # Model the pre-tournament ledger, including the original one-answer-per-feature UNIQUE.
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE jev_decisions")
        conn.exec_driver_sql("DROP TABLE accounts")
        conn.exec_driver_sql(
            "CREATE TABLE accounts (id INTEGER PRIMARY KEY, run_id VARCHAR(64) REFERENCES research_runs(id), strategy VARCHAR(32), cash FLOAT, day VARCHAR(10), day_start_equity FLOAT, halted BOOLEAN, UNIQUE(run_id,strategy))"
        )
        conn.exec_driver_sql(
            "CREATE TABLE jev_decisions (id INTEGER PRIMARY KEY, feature_id INTEGER UNIQUE REFERENCES features(id), timestamp DATETIME, request JSON, raw_response JSON, attempts JSON, model_version VARCHAR(80), status VARCHAR(32), latency_ms FLOAT, request_count INTEGER, estimated_cost FLOAT)"
        )
        cfg = load_config()
        cfg.tournament = None
        cfg.traders = []
        conn.execute(
            text(
                "INSERT INTO research_runs(id,mode,config,config_hash,paused,metadata_json) VALUES ('old-live','LIVE_PAPER',:cfg,'oldhash',0,'{}')"
            ),
            {"cfg": cfg.model_dump_json()},
        )
        conn.exec_driver_sql(
            "INSERT INTO accounts VALUES (71,'old-live','jev',987654.321,'2026-01-01',1000000,1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO candles(id,symbol,timeframe,timestamp,open,high,low,close,volume,provider) VALUES (81,'BTC/KRW','1m','2026-01-01 00:00:00',100,101,99,100,1,'test')"
        )
        conn.exec_driver_sql(
            "INSERT INTO features(id,run_id,candle_id,timestamp,state,config_hash) VALUES (91,'old-live',81,'2026-01-01 00:01:00','{}','oldhash')"
        )
        conn.execute(
            text(
                "INSERT INTO jev_decisions VALUES (101,91,'2026-01-01 00:01:03','{}',:raw,'[]','local:tev1:0.8b','OK',3000,1,NULL)"
            ),
            {"raw": json.dumps(response(0.8))},
        )
        conn.exec_driver_sql(
            "INSERT INTO strategy_decisions(id,run_id,feature_id,jev_decision_id,timestamp,strategy,strategy_version,action,reason,status) VALUES (111,'old-live',91,101,'2026-01-01 00:01:03','jev','legacy','HOLD','test','HOLD')"
        )
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
    migrate(engine)
    migrate(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert not conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        assert conn.exec_driver_sql(
            "SELECT version FROM schema_migrations"
        ).fetchall() == [(1,)]
    with Session(engine) as db:
        a = db.get(Account, 71)
        d = db.get(JevDecision, 101)
        assert (
            a.cash == 987654.321
            and a.halted
            and a.run_id == "old-live"
            and a.trader_id == "jev"
            and a.tournament_id is None
        )
        assert (
            d.raw_response == response(0.8)
            and d.feature_id == 91
            and d.model_version == "local:tev1:0.8b"
        )
        db.add(
            JevDecision(
                feature_id=91,
                trader_id="new-trader",
                request={},
                status="OK",
                latency_ms=1,
                request_count=0,
            )
        )
        db.flush()
        assert db.get(JevDecision, 101).raw_response == response(0.8)
    assert any(
        set(c["column_names"]) == {"feature_id", "trader_id"}
        for c in inspect(engine).get_unique_constraints("jev_decisions")
    )

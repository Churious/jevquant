"""Versioned, additive migration of the existing PostgreSQL/SQLite lab ledger."""

from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable, CreateIndex

SCOPED = (
    "accounts",
    "positions",
    "paper_orders",
    "paper_trades",
    "portfolio_snapshots",
    "jev_decisions",
    "strategy_decisions",
    "forward_returns",
)


def migrate(engine):
    if engine.dialect.name == "sqlite":
        # Toggle enforcement outside the transaction; restore the connection's setting.
        with engine.connect() as conn:
            enabled = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.commit()
            try:
                with conn.begin():
                    _migrate(conn, engine)
                    if conn.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("Migration violates existing foreign keys")
            finally:
                conn.exec_driver_sql(f"PRAGMA foreign_keys={int(enabled)}")
                conn.commit()
        return
    with engine.begin() as conn:
        _migrate(conn, engine)


def _migrate(conn, engine):
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
        )
    )
    if conn.scalar(text("SELECT version FROM schema_migrations WHERE version=1")):
        return
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if engine.dialect.name == "postgresql"
        else "DATETIME"
    )
    for table in SCOPED:
        columns = {c["name"] for c in inspect(conn).get_columns(table)}
        additions = {
            "tournament_id": "VARCHAR(64) REFERENCES tournaments(id)",
            "trader_id": "VARCHAR(32) NOT NULL DEFAULT 'jev'"
            if table == "jev_decisions"
            else "VARCHAR(32)",
        }
        if table == "accounts":
            additions.update(
                trader_record_id="INTEGER REFERENCES traders(id)",
                strategy_style="VARCHAR(32)",
            )
        if table == "jev_decisions":
            additions.update(
                {
                    key: timestamp_type
                    for key in (
                        "queued_at",
                        "deadline_at",
                        "decision_started_at",
                        "decision_completed_at",
                    )
                }
            )
        if table == "portfolio_snapshots":
            additions.update(
                {
                    key: "FLOAT"
                    for key in (
                        "exposure_value",
                        "crypto_exposure",
                        "stock_exposure",
                    )
                }
            )
        for key, ddl in additions.items():
            if key not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {key} {ddl}"))
    constraints = inspect(conn).get_unique_constraints("jev_decisions")
    old_unique = [c for c in constraints if c["column_names"] == ["feature_id"]]
    if engine.dialect.name == "postgresql":
        quote = engine.dialect.identifier_preparer.quote
        for constraint in old_unique:
            conn.execute(
                text(
                    f"ALTER TABLE jev_decisions DROP CONSTRAINT {quote(constraint['name'])}"
                )
            )
        if not any(
            set(c["column_names"]) == {"feature_id", "trader_id"} for c in constraints
        ):
            conn.execute(
                text(
                    "ALTER TABLE jev_decisions ADD CONSTRAINT uq_jev_feature_trader UNIQUE (feature_id,trader_id)"
                )
            )
    elif old_unique:
        # SQLite cannot drop an auto-index UNIQUE constraint in place.
        from .jev import JevDecision

        table = JevDecision.__table__
        ddl = str(CreateTable(table).compile(dialect=engine.dialect)).replace(
            "CREATE TABLE jev_decisions", "CREATE TABLE jev_decisions_v2", 1
        )
        conn.exec_driver_sql(ddl)
        names = ",".join(c.name for c in table.columns)
        conn.exec_driver_sql(
            f"INSERT INTO jev_decisions_v2 ({names}) SELECT {names} FROM jev_decisions"
        )
        conn.exec_driver_sql("DROP TABLE jev_decisions")
        conn.exec_driver_sql("ALTER TABLE jev_decisions_v2 RENAME TO jev_decisions")
        for index in table.indexes:
            conn.execute(CreateIndex(index))
    # Fill legacy identities without changing run IDs, prices, responses or balances.
    for table, expression in {
        "accounts": "strategy",
        "strategy_decisions": "strategy",
        "positions": "(SELECT strategy FROM accounts WHERE accounts.id=positions.account_id)",
        "paper_orders": "(SELECT strategy FROM accounts WHERE accounts.id=paper_orders.account_id)",
        "paper_trades": "(SELECT strategy FROM accounts WHERE accounts.id=paper_trades.account_id)",
        "portfolio_snapshots": "(SELECT strategy FROM accounts WHERE accounts.id=portfolio_snapshots.account_id)",
        "forward_returns": "(SELECT trader_id FROM jev_decisions WHERE jev_decisions.id=forward_returns.decision_id)",
    }.items():
        conn.execute(
            text(f"UPDATE {table} SET trader_id={expression} WHERE trader_id IS NULL")
        )
    conn.execute(text("INSERT INTO schema_migrations (version) VALUES (1)"))
    from .db import Base

    for table in SCOPED:
        for index in Base.metadata.tables[table].indexes:
            index.create(conn, checkfirst=True)

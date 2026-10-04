from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select, text

from .api import router
from .db import Session, SystemEvent, init_db
from .runner import runner
from .tournament_api import router as tournament_router


@asynccontextmanager
async def lifespan(app):
    init_db()
    await runner.start()
    yield
    await runner.stop()


app = FastAPI(title="Jev Trading Tournament", lifespan=lifespan)
app.include_router(router)
app.include_router(tournament_router)


@app.get("/health")
def health():
    with Session() as db:
        db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "paper_only": True,
        "worker": runner.status,
        "symbols": runner.symbol_status,
        "websocket": runner.ws_status,
        "run_id": runner.run_id,
        "decision_timeframe": runner.cfg.strategy.timeframe,
        "execution_timeframe": runner.cfg.market.execution_timeframe,
        "decision_provider": runner.decision_runtime.provider,
        "decision_model": runner.decision_runtime.model,
    }


@app.get("/logs")
def logs():
    with Session() as db:
        return [
            {
                "id": e.id,
                "timestamp": e.timestamp,
                "kind": e.kind,
                "level": e.level,
                "payload": e.payload,
            }
            for e in db.scalars(
                select(SystemEvent).order_by(SystemEvent.id.desc()).limit(100)
            )
        ]

"""Read-only model preflight against the latest real market feature snapshot."""

import asyncio
import json

import httpx
from sqlalchemy import select

from .config import load_config
from .db import Session
from .decision_runtime import DecisionRuntime
from .jev import JevClient
from .market import FeatureSnapshot


async def check():
    runtime = DecisionRuntime.from_env()
    with Session() as db:
        feature = db.scalar(
            select(FeatureSnapshot).order_by(FeatureSnapshot.timestamp.desc()).limit(1)
        )
        if not feature:
            raise SystemExit(
                "실제 시세 스냅샷이 없습니다. 데이터 수집 후 다시 실행하세요."
            )
        state = feature.state
    async with httpx.AsyncClient() as client:
        result = await JevClient(client, load_config(), runtime).evaluate(state)
    print(
        json.dumps(
            {
                "provider": runtime.provider,
                "requested_model": runtime.model,
                "status": result["status"],
                "model_version": result["model_version"],
                "latency_ms": round(result["latency_ms"]),
                "request_count": result["request_count"],
                "saved_to_ledger": False,
            },
            ensure_ascii=False,
        )
    )
    if result["status"] != "OK":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(check())

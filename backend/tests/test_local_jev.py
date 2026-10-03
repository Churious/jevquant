import json
import asyncio

import httpx
import pytest

from app.config import load_config
from app.decision_runtime import DecisionRuntime, bind_runtime
from app.jev import JevClient, JevDecision
from app.features import build_state
from app.replay import replay
from app.research import state_hash
from app.market import FeatureSnapshot
from app.trading import ensure_run
from sqlalchemy import select
from test_features import histories
from zoneinfo import ZoneInfo, reset_tzpath, TZPATH
from app.runner import Runner
from app.market import store_bar, Candle
from app.db import SystemEvent
from datetime import timedelta
from test_jev import response


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("JEV_PROVIDER", "local")
    for name in ("JEV_BASE_URL", "JEV_MODEL", "JEV_CONCURRENCY", "LOCAL_JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "cloud-secret-must-not-be-forwarded")


async def test_local_native_systemone_without_cloud_auth(local_env):
    raw = response()
    raw["model"] = "tev1:0.8b"
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "http://localhost:11434/v1/systemone"
        assert "authorization" not in request.headers
        assert json.loads(request.content)["model"] == "tev1:0.8b"
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevClient(client, load_config()).evaluate(
            {"price": {"current": 100}}
        )
    assert result["status"] == "OK"
    assert result["raw_response"] == raw
    assert result["model_version"] == "local:tev1:0.8b"
    assert result["estimated_cost"] == 0
    assert result["attempts"][0]["runtime"]["provider"] == "local"
    assert len(calls) == 1


async def test_local_optional_auth_is_separate(local_env, monkeypatch):
    monkeypatch.setenv("LOCAL_JEV_API_KEY", "local-test-token")
    monkeypatch.setenv("JEV_BASE_URL", "http://model-server:9000/v1/")

    def handler(request):
        assert request.headers["authorization"] == "Bearer local-test-token"
        assert str(request.url) == "http://model-server:9000/v1/systemone"
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await JevClient(client, load_config()).evaluate({}))["status"] == "OK"


async def test_failed_local_never_falls_back_to_cloud(local_env):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404, json={"error": "model missing"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevClient(client, load_config()).evaluate({})
    assert result["status"] == "JEV_UNAVAILABLE"
    assert result["model_version"] is None
    assert calls == ["http://localhost:11434/v1/systemone"]


async def test_local_invalid_probabilities_abstain(local_env):
    raw = response()
    raw["answers"]["long_setup"]["noul"] = 2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=raw))
    ) as client:
        result = await JevClient(client, load_config()).evaluate({})
    assert result["status"] == "INVALID_RESPONSE"
    assert result["raw_response"] == raw
    assert result["model_version"] is None


@pytest.mark.parametrize(
    "variable,value",
    [
        ("JEV_PROVIDER", "typo"),
        ("JEV_BASE_URL", "ftp://localhost"),
        ("JEV_BASE_URL", "http://user:secret@localhost"),
        ("JEV_BASE_URL", "http://localhost?key=secret"),
        ("JEV_MODEL", "".join(["a"] * 65)),
        ("JEV_CONCURRENCY", "0"),
        ("LOCAL_JEV_TIMEOUT_SECONDS", "nan"),
    ],
)
def test_bad_runtime_settings_rejected(local_env, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError):
        DecisionRuntime.from_env()


def test_local_experiment_identity_immutable(db, local_env, monkeypatch):
    cfg = load_config()
    run = ensure_run(db, "local-test", "LIVE_PAPER", cfg, "hash")
    runtime = DecisionRuntime.from_env()
    assert runtime.concurrency == 1
    assert runtime.timeout_seconds == 7
    bind_runtime(db, run, runtime)
    bind_runtime(db, run, runtime)
    for variable, value in [
        ("JEV_MODEL", "tev1:4b"),
        ("JEV_BASE_URL", "http://other:11434"),
        ("JEV_PROVIDER", "typesafe"),
    ]:
        with monkeypatch.context() as patch:
            patch.setenv(variable, value)
            with pytest.raises(ValueError, match="DECISION_RUNTIME_CHANGED"):
                bind_runtime(db, run, DecisionRuntime.from_env())


async def test_local_timeout_respects_total_scalping_budget(local_env):
    cfg = load_config()
    cfg.jev.max_evaluation_seconds = 0.02

    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        result = await JevClient(client, cfg).evaluate({})
    assert result["status"] == "JEV_UNAVAILABLE"
    assert result["request_count"] == 1
    assert result["latency_ms"] < 150


def test_replay_preserves_local_model_identity(db):
    cfg = load_config()
    data, now = histories()
    state = build_state("BTC/USDT", "1m", data, now, cfg)
    raw = response()
    raw["model"] = "tev1:0.8b"
    cache = {
        ("BTC/USDT", now, state_hash(state)): {
            "raw_response": raw,
            "model_version": "local:tev1:0.8b",
            "observed_at": now.isoformat(),
        }
    }
    replay(
        db,
        [b for group in data.values() for b in group],
        cache,
        cfg,
        "local-cache-replay",
    )
    decisions = list(
        db.scalars(
            select(JevDecision)
            .join(FeatureSnapshot)
            .where(
                FeatureSnapshot.run_id == "local-cache-replay",
                JevDecision.status == "OK",
            )
        )
    )
    assert len(decisions) == 1
    assert decisions[0].model_version == "local:tev1:0.8b"
    assert decisions[0].raw_response == raw


def test_korean_timezone_available_without_os_database():
    # Windows has no system IANA database; exercise the packaged fallback.
    try:
        reset_tzpath([])
        assert ZoneInfo.no_cache("Asia/Seoul").key == "Asia/Seoul"
    finally:
        reset_tzpath(TZPATH)


def test_provider_revision_preserves_canonical_history_and_stores_next_bar(db):
    data, now = histories()
    original = data["1m"][-1]
    stored = store_bar(db, original, "upbit")
    revised = original.model_copy(update={"volume": original.volume + 1})
    next_bar = original.model_copy(
        update={"timestamp": original.timestamp + timedelta(minutes=1)}
    )
    canonical = Runner.persist_history(db, [revised, next_bar], "upbit")
    db.flush()
    assert canonical == [original, next_bar]
    assert db.get(Candle, stored.id).volume == original.volume
    event = db.scalar(
        select(SystemEvent).where(SystemEvent.kind == "CANDLE_REVISION_IGNORED")
    )
    assert event.payload["observed_revision"]["volume"] == revised.volume
    assert (
        db.scalar(select(Candle).where(Candle.timestamp == next_bar.timestamp))
        is not None
    )

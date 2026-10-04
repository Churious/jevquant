import asyncio
import json
import time
from datetime import datetime, timedelta

import httpx
import pytest

from app.kiwoom_market import KiwoomProvider
from app.korean_market import KST, aggregate_kis
from app.market import providers


def row(stamp="20260930090000", price="-1000"):
    return {
        "cntr_tm": stamp,
        "cur_prc": price,
        "open_pric": "-1000",
        "high_pric": "-1010",
        "low_pric": "-990",
        "trde_qty": "10",
    }


def authorize(monkeypatch):
    monkeypatch.setenv("KIWOOM_APP_KEY", "test-key")
    monkeypatch.setenv("KIWOOM_SECRET_KEY", "test-secret")
    monkeypatch.setenv("KIWOOM_API_ENV", "live")


def token():
    return {
        "return_code": 0,
        "token": "private-token",
        "token_type": "bearer",
        "expires_dt": (datetime.now(KST) + timedelta(hours=24)).strftime(
            "%Y%m%d%H%M%S"
        ),
    }


async def test_missing_kiwoom_credentials_never_use_kis_keys(monkeypatch):
    monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
    monkeypatch.delenv("KIWOOM_SECRET_KEY", raising=False)
    monkeypatch.setenv("STOCK_API_KEY", "kis-key")
    monkeypatch.setenv("STOCK_API_SECRET", "kis-secret")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("network"))
    ) as client:
        with pytest.raises(RuntimeError, match="CREDENTIALS_MISSING"):
            await KiwoomProvider(client).history("005930", "1m", 60)


async def test_kiwoom_auth_pagination_and_only_allowlisted_prices(monkeypatch):
    authorize(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/oauth2/token":
            assert json.loads(request.content) == {
                "grant_type": "client_credentials",
                "appkey": "test-key",
                "secretkey": "test-secret",
            }
            return httpx.Response(200, json=token())
        assert request.url.host == "api.kiwoom.com"
        assert request.headers["api-id"] == "ka10080"
        assert request.headers["authorization"] == "Bearer private-token"
        assert json.loads(request.content) == {
            "stk_cd": "005930",
            "tic_scope": "1",
            "upd_stkpc_tp": "0",
        }
        if request.headers["cont-yn"] == "N":
            return httpx.Response(
                200,
                headers={"cont-yn": "Y", "next-key": "page2"},
                json={
                    "return_code": 0,
                    "stk_cd": "005930",
                    "stk_min_pole_chart_qry": [row()],
                },
            )
        assert request.headers["next-key"] == "page2"
        return httpx.Response(
            200,
            headers={"cont-yn": "N"},
            json={"return_code": 0, "stk_min_pole_chart_qry": [row("20260929090000")]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = KiwoomProvider(client)
        provider.interval = 0
        await provider.backfill("005930", 60)
        assert len(provider.rows["005930"]) == 2
        assert not provider.errors
    assert [(r.method, r.url.path) for r in calls] == [
        ("POST", "/oauth2/token"),
        ("POST", "/api/dostk/chart"),
        ("POST", "/api/dostk/chart"),
    ]


def test_kiwoom_signed_prices_full_coverage_and_no_future_bar():
    parsed = [KiwoomProvider.minute_row(row(f"20260930090{i}00")) for i in range(5)]
    now = datetime(2026, 9, 30, 9, 5, tzinfo=KST)
    bars = aggregate_kis(parsed, "069500", "5m", now)
    assert len(bars) == 1 and bars[0].open == 1000 and bars[0].volume == 50
    assert bars[0].high == 1010 and bars[0].low == 990
    assert not aggregate_kis(parsed[:-1], "069500", "5m", now)
    assert not aggregate_kis(parsed, "069500", "5m", now.replace(minute=4))
    assert not aggregate_kis(parsed[:1], "069500", "1m", now.replace(minute=0))
    with pytest.raises(ValueError, match="TIMESTAMP"):
        KiwoomProvider.minute_row(row("20260930090030"))


async def test_kiwoom_expired_token_refresh_and_errors_are_redacted(monkeypatch):
    authorize(monkeypatch)
    auths, charts = 0, 0

    def handler(request):
        nonlocal auths, charts
        if request.url.path == "/oauth2/token":
            auths += 1
            return httpx.Response(200, json=token())
        charts += 1
        if charts == 1:
            return httpx.Response(
                200, json={"return_code": 8005, "return_msg": "private-token"}
            )
        return httpx.Response(
            200, json={"return_code": 0, "stk_min_pole_chart_qry": []}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = KiwoomProvider(client)
        provider.interval = 0
        await provider.request_minutes("005930")
        assert auths == 2 and charts == 2
        provider.expires = time.monotonic() - 1
        await provider.request_minutes("005930")
        assert auths == 3
    response = httpx.Response(
        200,
        json={"return_code": 8001, "return_msg": "test-secret"},
        request=httpx.Request("POST", "https://api.kiwoom.com/api/dostk/chart"),
    )
    with pytest.raises(RuntimeError, match="KIWOOM_API_ERROR") as error:
        KiwoomProvider.check_response(response)
    assert "test-secret" not in str(error.value)


async def test_kiwoom_repeated_pagination_backs_off(monkeypatch):
    authorize(monkeypatch)

    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=token())
        return httpx.Response(
            200,
            headers={"cont-yn": "Y", "next-key": "same"},
            json={"return_code": 0, "stk_min_pole_chart_qry": [row()]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = KiwoomProvider(client)
        provider.interval = 0
        await provider.backfill("005930", 60)
        assert provider.errors["005930"] == "ValueError"
        with pytest.raises(RuntimeError, match="BACKFILL_ERROR"):
            await provider.history("005930", "1h", 60)


async def test_kiwoom_background_history_keeps_market_loop_free(monkeypatch):
    authorize(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return (
            httpx.Response(200, json=token())
            if request.url.path == "/oauth2/token"
            else httpx.Response(
                200, json={"return_code": 0, "stk_min_pole_chart_qry": []}
            )
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = KiwoomProvider(client)
        provider.interval = 0
        assert (
            await asyncio.wait_for(provider.history("005930", "1m", 200), timeout=0.5)
            == []
        )
        await asyncio.wait_for(entered.wait(), timeout=0.5)
        assert not provider.jobs["005930"].done()
        release.set()
        await provider.jobs["005930"]
        await provider.close()


async def test_kiwoom_factory_mock_env_and_krx_only(monkeypatch):
    authorize(monkeypatch)
    monkeypatch.setenv("STOCK_DATA_PROVIDER", "kiwoom")
    monkeypatch.setenv("KIWOOM_API_ENV", "mock")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("network"))
    ) as client:
        _, provider = providers(client)
        assert isinstance(provider, KiwoomProvider)
        assert provider.base == "https://mockapi.kiwoom.com" and provider.interval >= 1
        with pytest.raises(ValueError, match="KRX_SYMBOL"):
            await provider.request_minutes("005930_NX")
        monkeypatch.setenv("KIWOOM_API_ENV", "untrusted")
        with pytest.raises(ValueError, match="API_ENV"):
            KiwoomProvider(client)


async def test_kiwoom_429_cooldown_and_wrong_symbol(monkeypatch):
    authorize(monkeypatch)
    mode = "rate"

    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=token())
        if mode == "rate":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "return_code": 0,
                "stk_cd": "000660",
                "stk_min_pole_chart_qry": [row()],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = KiwoomProvider(client)
        provider.interval = 0
        await provider.backfill("005930", 60)
        assert provider.retry_after["005930"] > time.monotonic()
        with pytest.raises(RuntimeError, match="BACKFILL_ERROR"):
            await provider.history("005930", "1m", 60)
        mode = "symbol"
        with pytest.raises(ValueError, match="SYMBOL_MISMATCH"):
            await provider.request_minutes("005930")

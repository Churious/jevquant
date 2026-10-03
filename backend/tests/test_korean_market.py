from datetime import datetime

import httpx

from app.korean_market import aggregate_kis, KISProvider, UpbitProvider, KST
from app.trading import PaperBroker
from test_trading import setup


def test_korean_aggregation_requires_complete_minutes():
    rows = [
        {
            "stck_bsop_date": "20260930",
            "stck_cntg_hour": f"090{i}00",
            "stck_oprc": "1000",
            "stck_hgpr": "1010",
            "stck_lwpr": "990",
            "stck_prpr": "1005",
            "cntg_vol": "10",
        }
        for i in range(5)
    ]
    now = datetime(2026, 9, 30, 9, 5, tzinfo=KST)
    bars = aggregate_kis(rows, "069500", "5m", now)
    assert len(bars) == 1 and bars[0].volume == 50 and bars[0].close == 1005
    assert not aggregate_kis(rows[:-1], "069500", "5m", now)
    assert not aggregate_kis(rows, "069500", "5m", now.replace(minute=4))


async def test_kis_only_allowlisted_auth_and_quote_paths(monkeypatch):
    monkeypatch.setenv("STOCK_API_KEY", "mock-app-key")
    monkeypatch.setenv("STOCK_API_SECRET", "mock-app-secret")
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(
            200,
            json={"access_token": "mock-token", "expires_in": 86400}
            if r.url.path == "/oauth2/tokenP"
            else {"rt_cd": "0", "output2": []},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await KISProvider(client).request_minutes(
            "005930", datetime(2026, 9, 30).date(), "153000"
        )
    assert [(r.method, r.url.path) for r in calls] == [
        ("POST", "/oauth2/tokenP"),
        ("GET", "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"),
    ]
    assert not any("trading" in r.url.path or "order" in r.url.path for r in calls)


async def test_upbit_krw_closed_candles(monkeypatch):
    def handler(r):
        assert r.url.params["market"] == "KRW-BTC" and r.method == "GET"
        return httpx.Response(
            200,
            json=[
                {
                    "candle_date_time_utc": "2026-09-30T00:00:00",
                    "opening_price": 100000000,
                    "high_price": 100100000,
                    "low_price": 99900000,
                    "trade_price": 100000000,
                    "candle_acc_trade_volume": 1,
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bars = await UpbitProvider(client).history("BTC/KRW", "5m", 1)
    assert len(bars) == 1 and bars[0].symbol == "BTC/KRW"


def test_whole_stock_shares_and_no_stock_short(db):
    cfg, account, bar, decision = setup(db)
    broker = PaperBroker(cfg)
    positions = []
    bar = bar.model_copy(update={"symbol": "069500"})
    assert (
        broker.open(db, account, positions, decision, bar, {"volatility": {"atr14": 2}})
        == "EXECUTED"
    )
    assert positions[0].quantity == int(positions[0].quantity)
    decision.action = "SHORT"
    assert (
        broker.open(db, account, [], decision, bar, {"volatility": {"atr14": 2}})
        == "STOCK_SHORT_DISABLED"
    )

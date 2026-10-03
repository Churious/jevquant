from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.market import CryptoProvider, StockProvider


async def test_crypto_public_host_closed_bars_only():
    now = datetime.now(timezone.utc)
    seen = []

    def handler(r):
        seen.append(r)
        return httpx.Response(
            200,
            json=[
                [
                    int((now - timedelta(minutes=30)).timestamp() * 1000),
                    "100",
                    "101",
                    "99",
                    "100",
                    "10",
                ],
                [int(now.timestamp() * 1000), "100", "101", "99", "100", "10"],
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bars = await CryptoProvider(client).history("BTC/USDT", "15m", 200)
    assert len(bars) == 1
    assert seen[0].url.host == "data-api.binance.vision"
    assert seen[0].method == "GET"
    assert "authorization" not in seen[0].headers


async def test_stock_missing_key_never_calls_api(monkeypatch):
    monkeypatch.delenv("STOCK_API_KEY", raising=False)
    monkeypatch.delenv("STOCK_API_SECRET", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("network call"))
    ) as client:
        with pytest.raises(RuntimeError, match="CREDENTIALS_MISSING"):
            await StockProvider(client).history("SPY", "15m", 200)


async def test_stock_data_pagination(monkeypatch):
    monkeypatch.setenv("STOCK_API_KEY", "test-key")
    monkeypatch.setenv("STOCK_API_SECRET", "test-secret")
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(
            200,
            json={
                "bars": {
                    "SPY": [
                        {
                            "t": f"2026-01-01T0{len(calls)}:00:00Z",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100,
                            "v": 10,
                        }
                    ]
                },
                "next_page_token": "page2" if len(calls) == 1 else None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bars = await StockProvider(client).history("SPY", "1h", 2)
    assert len(bars) == 2
    assert calls[1].url.params["page_token"] == "page2"
    assert all(r.url.host == "data.alpaca.markets" and r.method == "GET" for r in calls)

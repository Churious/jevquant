"""US regular-session minute prices, separate credentials, no trading endpoints."""

import asyncio
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .market import StockProvider, Bar, MINUTES

NY = ZoneInfo("America/New_York")


def aggregate_us(bars, symbol, timeframe, now):
    minutes = MINUTES[timeframe]
    groups = defaultdict(dict)
    for bar in bars:
        local = bar.timestamp.astimezone(NY)
        offset = local.hour * 60 + local.minute - 570
        if local.weekday() >= 5 or not 0 <= offset < 390 or local.second:
            continue
        start = local.replace(hour=9, minute=30, second=0, microsecond=0)
        bucket = start + timedelta(minutes=(offset // minutes) * minutes)
        groups[bucket][local] = bar
    result = []
    for stamp, members in sorted(groups.items()):
        expected = [stamp + timedelta(minutes=i) for i in range(minutes)]
        if stamp + timedelta(minutes=minutes) > now or any(
            t not in members for t in expected
        ):
            continue
        data = [members[t] for t in expected]
        result.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=stamp,
                open=data[0].open,
                high=max(b.high for b in data),
                low=min(b.low for b in data),
                close=data[-1].close,
                volume=sum(b.volume for b in data),
            )
        )
    return result


class USStockProvider(StockProvider):
    name = "alpaca-iex"

    def __init__(self, client):
        super().__init__(
            client,
            key=os.getenv("ALPACA_API_KEY", ""),
            secret=os.getenv("ALPACA_API_SECRET", ""),
        )
        self.feed = "iex"
        self.rows = defaultdict(dict)
        self.jobs = {}
        self.retry_after = {}
        self.refreshed = {}
        self.lock = asyncio.Lock()
        self.last_request = 0

    async def close(self):
        for task in self.jobs.values():
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def source(self, symbol, limit):
        async with self.lock:
            return await super().history(symbol, "1m", limit)

    async def before_request(self):
        await asyncio.sleep(max(0, 0.4 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()

    def remember(self, symbol, bars):
        for bar in bars:
            self.rows[symbol][bar.timestamp] = bar
        self.rows[symbol] = dict(sorted(self.rows[symbol].items())[-25000:])

    async def backfill(self, symbol):
        try:
            self.remember(symbol, await self.source(symbol, 22000))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.retry_after[symbol] = time.monotonic() + 60

    async def history(self, symbol, timeframe, limit):
        if not self.key or not self.secret:
            raise RuntimeError("ALPACA_CREDENTIALS_MISSING")
        if symbol in self.retry_after:
            if time.monotonic() < self.retry_after[symbol]:
                raise RuntimeError("ALPACA_BACKFILL_ERROR")
            self.retry_after.pop(symbol)
            self.jobs.pop(symbol, None)
        if symbol not in self.jobs:
            self.jobs[symbol] = asyncio.create_task(self.backfill(symbol))
        now = datetime.now(timezone.utc)
        local = now.astimezone(NY)
        if (
            self.jobs[symbol].done()
            and local.weekday() < 5
            and 570 <= local.hour * 60 + local.minute <= 960
            and time.monotonic() - self.refreshed.get(symbol, 0) > 5
        ):
            self.remember(symbol, await self.source(symbol, 3))
            self.refreshed[symbol] = time.monotonic()
        return aggregate_us(self.rows[symbol].values(), symbol, timeframe, now)[-limit:]

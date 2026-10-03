import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import websockets

from .market import Bar, MarketDataProvider, MINUTES

KST = ZoneInfo("Asia/Seoul")


class UpbitProvider(MarketDataProvider):
    name = "upbit"

    def __init__(self, client):
        self.client = client
        self.last_request = 0
        self.request_lock = asyncio.Lock()

    @staticmethod
    def parse(row, symbol, tf):
        return Bar(
            symbol=symbol,
            timeframe=tf,
            timestamp=row["candle_date_time_utc"] + "Z",
            open=row["opening_price"],
            high=row["high_price"],
            low=row["low_price"],
            close=row["trade_price"],
            volume=row["candle_acc_trade_volume"],
        )

    async def history(self, symbol, timeframe, limit):
        coin, quote = symbol.split("/")
        params = {"market": f"{quote}-{coin}", "count": min(200, limit)}
        result = {}
        now = datetime.now(timezone.utc)
        while len(result) < limit:
            async with self.request_lock:
                await asyncio.sleep(
                    max(0, 0.13 - (time.monotonic() - self.last_request))
                )
                self.last_request = time.monotonic()
                r = await self.client.get(
                    f"https://api.upbit.com/v1/candles/minutes/{MINUTES[timeframe]}",
                    params=params,
                )
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            for row in rows:
                bar = self.parse(row, symbol, timeframe)
                if bar.end <= now:
                    result[bar.timestamp] = bar
            before = params.get("to")
            params["to"] = rows[-1]["candle_date_time_utc"] + "Z"
            if before == params["to"]:
                break
            params["count"] = min(200, limit - len(result))
        return sorted(result.values(), key=lambda b: b.timestamp)[-limit:]

    async def stream(self, symbols, timeframes, on_bar):
        mapping = {f"{s.split('/')[1]}-{s.split('/')[0]}": s for s in symbols}
        request = (
            [{"ticket": "jev-paper-lab"}]
            + [
                {"type": f"candle.{MINUTES[tf]}m", "codes": list(mapping)}
                for tf in timeframes
            ]
            + [{"format": "DEFAULT"}]
        )
        current = {}
        sent = {}
        async with websockets.connect(
            "wss://api.upbit.com/websocket/v1", ping_interval=20, ping_timeout=20
        ) as ws:
            await ws.send(json.dumps(request))
            while True:
                row = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
                if "error" in row:
                    raise ValueError("UPBIT_STREAM_ERROR")
                tf = {f"candle.{m}m": t for t, m in MINUTES.items()}[row["type"]]
                bar = self.parse(row, mapping[row["code"]], tf)
                key = (bar.symbol, tf)
                old = current.get(key)
                if (
                    old
                    and old.timestamp < bar.timestamp
                    and sent.get(key) != old.timestamp
                ):
                    await on_bar(old)  # next candle proves prior candle has completed
                    sent[key] = old.timestamp
                if not old or bar.timestamp >= old.timestamp:
                    current[key] = bar
                if (
                    bar.end <= datetime.now(timezone.utc)
                    and sent.get(key) != bar.timestamp
                ):
                    await on_bar(bar)
                    sent[key] = bar.timestamp


def aggregate_kis(rows, symbol, tf, now):
    groups = defaultdict(dict)
    minutes = MINUTES[tf]
    for row in rows:
        timestamp = datetime.strptime(
            row["stck_bsop_date"] + row["stck_cntg_hour"], "%Y%m%d%H%M%S"
        ).replace(tzinfo=KST)
        minute = timestamp.hour * 60 + timestamp.minute
        if not 540 <= minute < 930:
            continue
        bucket = timestamp.replace(
            hour=9, minute=0, second=0, microsecond=0
        ) + timedelta(minutes=((minute - 540) // minutes) * minutes)
        groups[bucket][timestamp] = row
    out = []
    for timestamp, members in sorted(groups.items()):
        # Reject incomplete minute coverage, including the final partial 1h session bar.
        expected = [timestamp + timedelta(minutes=i) for i in range(minutes)]
        if timestamp + timedelta(minutes=minutes) > now or any(
            t not in members for t in expected
        ):
            continue
        data = [members[t] for t in expected]
        out.append(
            Bar(
                symbol=symbol,
                timeframe=tf,
                timestamp=timestamp.astimezone(timezone.utc),
                open=data[0]["stck_oprc"],
                high=max(float(x["stck_hgpr"]) for x in data),
                low=min(float(x["stck_lwpr"]) for x in data),
                close=data[-1]["stck_prpr"],
                volume=sum(float(x["cntg_vol"]) for x in data),
            )
        )
    return out


class KISProvider(MarketDataProvider):
    """Allowlisted authentication + price queries. No account id or trading endpoints."""

    name = "kis"
    base = "https://openapi.koreainvestment.com:9443"

    def __init__(self, client):
        self.client = client
        self.key = os.getenv("STOCK_API_KEY", "")
        self.secret = os.getenv("STOCK_API_SECRET", "")
        self.token = None
        self.expires = 0
        self.last_request = 0
        self.lock = asyncio.Lock()
        self.rows = defaultdict(dict)
        self.jobs = {}
        self.errors = {}
        self.refreshed = {}
        self.retry_after = {}

    async def close(self):
        for job in self.jobs.values():
            job.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def request_minutes(self, symbol, day, hour):
        if not self.key or not self.secret:
            raise RuntimeError("STOCK_CREDENTIALS_MISSING")
        async with self.lock:
            if not self.token or time.monotonic() > self.expires:
                r = await self.client.post(
                    self.base + "/oauth2/tokenP",
                    json={
                        "grant_type": "client_credentials",
                        "appkey": self.key,
                        "appsecret": self.secret,
                    },
                )
                r.raise_for_status()
                body = r.json()
                self.token = body["access_token"]
                self.expires = time.monotonic() + max(
                    0, int(body.get("expires_in", 86400)) - 60
                )
            await asyncio.sleep(max(0, 0.55 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            r = await self.client.get(
                self.base
                + "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
                headers={
                    "authorization": f"Bearer {self.token}",
                    "appkey": self.key,
                    "appsecret": self.secret,
                    "tr_id": "FHKST03010230",
                    "custtype": "P",
                },
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": hour,
                    "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
                    "FID_PW_DATA_INCU_YN": "Y",
                    "FID_FAKE_TICK_INCU_YN": "",
                },
            )
            r.raise_for_status()
            body = r.json()
            if body.get("rt_cd") != "0":
                raise RuntimeError(
                    "KIS_DATA_ERROR:" + str(body.get("msg_cd", "unknown"))
                )
            return body.get("output2", [])

    async def fetch_day(self, symbol, day):
        hour = "153000"
        seen = set()
        for _ in range(20):
            rows = await self.request_minutes(symbol, day, hour)
            dated = [r for r in rows if r["stck_bsop_date"] == day.strftime("%Y%m%d")]
            if not dated:
                break
            for row in dated:
                self.rows[symbol][(row["stck_bsop_date"], row["stck_cntg_hour"])] = row
            earliest = min(r["stck_cntg_hour"] for r in dated)
            if earliest in seen or earliest <= "090000":
                break
            seen.add(earliest)
            hour = (
                datetime.strptime(earliest, "%H%M%S") - timedelta(minutes=1)
            ).strftime("%H%M%S")

    async def backfill(self, symbol, limit):
        try:
            now = datetime.now(KST)
            for offset in range(65):
                day = (now - timedelta(days=offset)).date()
                if day.weekday() < 5:
                    await self.fetch_day(symbol, day)
                if (
                    len(aggregate_kis(self.rows[symbol].values(), symbol, "1h", now))
                    >= limit
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.errors[symbol] = type(exc).__name__
            self.retry_after[symbol] = time.monotonic() + 60

    async def history(self, symbol, timeframe, limit):
        if not self.key or not self.secret:
            raise RuntimeError("STOCK_CREDENTIALS_MISSING")
        if symbol in self.errors:
            if time.monotonic() < self.retry_after[symbol]:
                raise RuntimeError("KIS_BACKFILL_ERROR")
            self.errors.pop(symbol)
            self.jobs.pop(symbol, None)
        if symbol not in self.jobs:
            self.jobs[symbol] = asyncio.create_task(
                self.backfill(symbol, max(limit, 212))
            )
        now = datetime.now(KST)
        if (
            self.jobs[symbol].done()
            and now.weekday() < 5
            and 540 <= now.hour * 60 + now.minute <= 930
        ):
            if time.monotonic() - self.refreshed.get(symbol, 0) > 5:
                rows = await self.request_minutes(
                    symbol, now.date(), now.strftime("%H%M%S")
                )
                for row in rows:
                    self.rows[symbol][
                        (row["stck_bsop_date"], row["stck_cntg_hour"])
                    ] = row
                self.refreshed[symbol] = time.monotonic()
        return aggregate_kis(self.rows[symbol].values(), symbol, timeframe, now)[
            -limit:
        ]

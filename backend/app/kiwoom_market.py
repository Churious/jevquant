"""Kiwoom REST authentication and KRX minute prices only (no account/order API)."""

import asyncio
import os
import re
import time
from collections import defaultdict
from datetime import datetime

from .korean_market import KST, aggregate_kis
from .market import MarketDataProvider


class KiwoomProvider(MarketDataProvider):
    name = "kiwoom"

    def __init__(self, client):
        self.client = client
        self.key = os.getenv("KIWOOM_APP_KEY", "").strip()
        self.secret = os.getenv("KIWOOM_SECRET_KEY", "").strip()
        environment = os.getenv("KIWOOM_API_ENV", "live").strip().lower()
        if environment not in {"live", "mock"}:
            raise ValueError("KIWOOM_API_ENV must be live or mock")
        self.base = (
            "https://api.kiwoom.com"
            if environment == "live"
            else "https://mockapi.kiwoom.com"
        )
        self.interval = 0.3 if environment == "live" else 1.1
        self.lock = asyncio.Lock()
        self.token = None
        self.expires = 0
        self.last_request = 0
        self.rows = defaultdict(dict)
        self.jobs = {}
        self.errors = {}
        self.retry_after = {}
        self.refreshed = {}

    async def close(self):
        for task in self.jobs.values():
            task.cancel()
        await asyncio.gather(*self.jobs.values(), return_exceptions=True)

    async def pace(self):
        await asyncio.sleep(
            max(0, self.interval - (time.monotonic() - self.last_request))
        )
        self.last_request = time.monotonic()

    @staticmethod
    def check_response(response):
        # Never include broker response messages, tokens or submitted credentials in errors.
        if response.status_code == 429:
            raise RuntimeError("KIWOOM_RATE_LIMIT")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("KIWOOM_INVALID_RESPONSE")
        if str(body.get("return_code", "0")) != "0":
            raise RuntimeError("KIWOOM_API_ERROR")
        return body

    async def authenticate(self):
        await self.pace()
        response = await self.client.post(
            self.base + "/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": self.key,
                "secretkey": self.secret,
            },
        )
        body = self.check_response(response)
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("KIWOOM_INVALID_TOKEN")
        expires = datetime.strptime(body["expires_dt"], "%Y%m%d%H%M%S").replace(
            tzinfo=KST
        )
        remaining = (expires - datetime.now(KST)).total_seconds() - 60
        if remaining <= 0:
            raise ValueError("KIWOOM_EXPIRED_TOKEN")
        self.token, self.expires = token, time.monotonic() + remaining

    async def request_minutes(self, symbol, next_key=None):
        if not self.key or not self.secret:
            raise RuntimeError("STOCK_CREDENTIALS_MISSING")
        if not re.fullmatch(r"[0-9]{6}", symbol):
            raise ValueError("KIWOOM_KRX_SYMBOL_REQUIRED")
        async with self.lock:
            for attempt in range(2):
                if not self.token or time.monotonic() >= self.expires:
                    await self.authenticate()
                await self.pace()
                response = await self.client.post(
                    self.base + "/api/dostk/chart",
                    headers={
                        "api-id": "ka10080",
                        "authorization": f"Bearer {self.token}",
                        "cont-yn": "Y" if next_key else "N",
                        "next-key": next_key or "",
                    },
                    json={"stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0"},
                )
                body = response.json() if response.status_code == 200 else {}
                if attempt == 0 and (
                    response.status_code == 401
                    or isinstance(body, dict)
                    and str(body.get("return_code")) == "8005"
                ):
                    self.token, self.expires = None, 0
                    continue
                body = self.check_response(response)
                if body.get("stk_cd", symbol) not in {symbol, "A" + symbol}:
                    raise ValueError("KIWOOM_SYMBOL_MISMATCH")
                rows = body.get("stk_min_pole_chart_qry", [])
                if not isinstance(rows, list):
                    raise ValueError("KIWOOM_INVALID_CANDLES")
                key = response.headers.get("next-key")
                if response.headers.get("cont-yn") == "Y" and not key:
                    raise ValueError("KIWOOM_MISSING_CONTINUATION")
                return rows, key if response.headers.get("cont-yn") == "Y" else None
        raise RuntimeError("KIWOOM_AUTH_FAILED")

    @staticmethod
    def minute_row(row):
        stamp = datetime.strptime(row["cntr_tm"], "%Y%m%d%H%M%S").replace(tzinfo=KST)
        if stamp.second != 0:
            raise ValueError("KIWOOM_INVALID_MINUTE_TIMESTAMP")
        # Price signs describe change versus yesterday; they are not negative prices.
        prices = {
            key: abs(float(row[source]))
            for key, source in {
                "stck_oprc": "open_pric",
                "stck_hgpr": "high_pric",
                "stck_lwpr": "low_pric",
                "stck_prpr": "cur_prc",
            }.items()
        }
        return {
            "stck_bsop_date": stamp.strftime("%Y%m%d"),
            "stck_cntg_hour": stamp.strftime("%H%M%S"),
            **prices,
            "cntg_vol": float(row["trde_qty"]),
        }

    def remember(self, symbol, rows):
        for row in rows:
            parsed = self.minute_row(row)
            self.rows[symbol][(parsed["stck_bsop_date"], parsed["stck_cntg_hour"])] = (
                parsed
            )
        # Bound the in-memory source cache without manufacturing missing minutes.
        if len(self.rows[symbol]) > 25000:
            self.rows[symbol] = dict(sorted(self.rows[symbol].items())[-25000:])

    async def backfill(self, symbol, limit):
        key, seen = None, set()
        try:
            for _ in range(256):
                rows, key = await self.request_minutes(symbol, key)
                self.remember(symbol, rows)
                if (
                    len(
                        aggregate_kis(
                            self.rows[symbol].values(), symbol, "1h", datetime.now(KST)
                        )
                    )
                    >= limit
                ):
                    return
                if not rows or key is None:
                    return
                if key in seen:
                    raise ValueError("KIWOOM_REPEATED_CONTINUATION")
                seen.add(key)
            raise RuntimeError("KIWOOM_BACKFILL_LIMIT")
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
                raise RuntimeError("KIWOOM_BACKFILL_ERROR")
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
                rows, _ = await self.request_minutes(symbol)
                self.remember(symbol, rows)
                self.refreshed[symbol] = time.monotonic()
        return aggregate_kis(self.rows[symbol].values(), symbol, timeframe, now)[
            -limit:
        ]

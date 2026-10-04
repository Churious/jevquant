import asyncio
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import httpx
import websockets
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, json_type

MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


def aware(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class Bar(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    symbol: str
    timeframe: str
    timestamp: datetime  # UTC open time; available only at end.
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_bar(self):
        if self.timestamp.tzinfo is None:
            raise ValueError("provider timestamps must have timezone")
        self.timestamp = aware(self.timestamp)
        if self.timeframe not in MINUTES:
            raise ValueError("unsupported timeframe")
        if (
            not self.low
            <= min(self.open, self.close)
            <= max(self.open, self.close)
            <= self.high
        ):
            raise ValueError("invalid OHLC range")
        return self

    @property
    def end(self):
        return self.timestamp + timedelta(minutes=MINUTES[self.timeframe])


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "timestamp"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(40))

    def bar(self):
        return Bar(
            **{k: getattr(self, k) for k in Bar.model_fields if k != "timestamp"},
            timestamp=aware(self.timestamp),
        )


class FeatureSnapshot(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("run_id", "candle_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    candle_id: Mapped[int] = mapped_column(ForeignKey("candles.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(json_type)
    config_hash: Mapped[str] = mapped_column(String(64))


def is_stale(bar: Bar, now: datetime, multiplier: float = 2, future_tolerance: int = 5):
    age = (aware(now) - bar.end).total_seconds()
    return age < -future_tolerance or age > MINUTES[bar.timeframe] * 60 * multiplier


def store_bar(db, bar: Bar, provider: str):
    old = db.scalar(
        select(Candle).where(
            Candle.symbol == bar.symbol,
            Candle.timeframe == bar.timeframe,
            Candle.timestamp == bar.timestamp,
        )
    )
    if old:
        if any(
            getattr(old, key) != getattr(bar, key)
            for key in ["open", "high", "low", "close", "volume"]
        ):
            raise ValueError("CANDLE_CONFLICT: closed observations are immutable")
        return old
    row = Candle(**bar.model_dump(), provider=provider)
    db.add(row)
    db.flush()
    return row


class MarketDataProvider(ABC):
    @abstractmethod
    async def history(self, symbol: str, timeframe: str, limit: int) -> list[Bar]: ...


class CryptoProvider(MarketDataProvider):
    """Only Binance's unauthenticated market-data hosts. No order API."""

    name = "binance"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def history(self, symbol, timeframe, limit):
        r = await self.client.get(
            "https://data-api.binance.vision/api/v3/klines",
            params={
                "symbol": symbol.replace("/", ""),
                "interval": timeframe,
                "limit": limit,
            },
        )
        r.raise_for_status()
        now = datetime.now(timezone.utc)
        bars = [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(x[0] / 1000, timezone.utc),
                open=x[1],
                high=x[2],
                low=x[3],
                close=x[4],
                volume=x[5],
            )
            for x in r.json()
        ]
        return [b for b in bars if b.end <= now]

    async def stream(self, symbols, timeframes, on_bar):
        streams = "/".join(
            f"{s.replace('/', '').lower()}@kline_{tf}"
            for s in symbols
            for tf in timeframes
        )
        mapping = {s.replace("/", ""): s for s in symbols}
        async with websockets.connect(
            f"wss://data-stream.binance.vision:443/stream?streams={streams}",
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
        ) as ws:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=90))
                k = msg["data"]["k"]
                if k["x"]:
                    await on_bar(
                        Bar(
                            symbol=mapping[k["s"]],
                            timeframe=k["i"],
                            timestamp=datetime.fromtimestamp(
                                k["t"] / 1000, timezone.utc
                            ),
                            open=k["o"],
                            high=k["h"],
                            low=k["l"],
                            close=k["c"],
                            volume=k["v"],
                        )
                    )


class StockProvider(MarketDataProvider):
    """Alpaca data host; IEX entitlement, pagination and closed bars only."""

    name = "alpaca"

    def __init__(self, client: httpx.AsyncClient, key=None, secret=None):
        self.client = client
        self.key = os.getenv("STOCK_API_KEY", "") if key is None else key
        self.secret = os.getenv("STOCK_API_SECRET", "") if secret is None else secret
        self.feed = os.getenv("STOCK_FEED", "iex")

    async def before_request(self):
        pass

    async def history(self, symbol, timeframe, limit):
        if not self.key or not self.secret:
            raise RuntimeError("STOCK_CREDENTIALS_MISSING")
        now = datetime.now(timezone.utc)
        params = {
            "symbols": symbol,
            "timeframe": {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour"}[
                timeframe
            ],
            "start": (now - timedelta(days=75)).isoformat(),
            "end": now.isoformat(),
            "feed": self.feed,
            "adjustment": "raw",
            "sort": "desc",
            "limit": min(limit, 10000),
        }
        bars = []
        seen = set()
        while len(bars) < limit:
            await self.before_request()
            r = await self.client.get(
                "https://data.alpaca.markets/v2/stocks/bars",
                params=params,
                headers={
                    "APCA-API-KEY-ID": self.key,
                    "APCA-API-SECRET-KEY": self.secret,
                },
            )
            r.raise_for_status()
            body = r.json()
            for x in body.get("bars", {}).get(symbol, []):
                bar = Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=x["t"],
                    open=x["o"],
                    high=x["h"],
                    low=x["l"],
                    close=x["c"],
                    volume=x["v"],
                )
                if bar.end <= now:
                    bars.append(bar)
            token = body.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ValueError("ALPACA_REPEATED_CONTINUATION")
            seen.add(token)
            params["page_token"] = token
        return sorted(bars[:limit], key=lambda b: b.timestamp)


def providers(client):
    from .korean_market import KISProvider, UpbitProvider
    from .kiwoom_market import KiwoomProvider

    crypto = {"binance": CryptoProvider, "upbit": UpbitProvider}.get(
        os.getenv("CRYPTO_DATA_PROVIDER", "upbit")
    )
    stock = {"alpaca": StockProvider, "kis": KISProvider, "kiwoom": KiwoomProvider}.get(
        os.getenv("STOCK_DATA_PROVIDER", "kis")
    )
    if not crypto or not stock:
        raise ValueError("unsupported market data provider")
    return crypto(client), stock(client)

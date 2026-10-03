import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Market(StrictConfig):
    crypto_symbols: list[str]
    stock_symbols: list[str]
    timeframes: list[str]
    history_bars: int = Field(ge=60, le=1000)
    poll_seconds: int = Field(gt=0)
    stale_multiplier: float = Field(ge=1)
    future_tolerance_seconds: int = Field(ge=0)
    execution_timeframe: str = "5m"


class Trading(StrictConfig):
    starting_capital: float = Field(gt=0)
    base_currency: str = "KRW"
    timezone: str = "Asia/Seoul"
    allow_stock_shorts: bool = False
    risk_per_trade: float = Field(gt=0, le=0.1)
    max_position_allocation: float = Field(gt=0, le=1)
    max_positions: int = Field(gt=0)
    daily_loss_limit: float = Field(gt=0, le=1)


class Strategy(StrictConfig):
    version: str
    timeframe: str
    long_threshold: float = Field(ge=0, le=1)
    short_threshold: float = Field(ge=0, le=1)
    avoid_threshold: float = Field(ge=0, le=1)
    min_setup_score: float = Field(ge=0, le=4)
    max_reversal_score: float = Field(ge=0, le=4)
    rsi_entry: float = Field(ge=0, le=100)
    rsi_exit: float = Field(ge=0, le=100)
    max_holding_minutes: int = Field(default=0, ge=0)


class Risk(StrictConfig):
    atr_stop_multiplier: float = Field(gt=0)
    reward_risk_ratio: float = Field(gt=0)


class Simulation(StrictConfig):
    fee_rate: float = Field(ge=0, lt=1)
    slippage_bps: float = Field(ge=0, lt=10000)
    min_net_target_return: float = Field(default=0, ge=0, lt=1)


class Jev(StrictConfig):
    timeout_seconds: float = Field(gt=0)
    attempts: int = Field(ge=1, le=5)
    backoff_seconds: float = Field(gt=0)
    input_cost_per_million: float = Field(ge=0)
    max_evaluation_seconds: float = Field(default=60, gt=0)


class Features(StrictConfig):
    minimum_bars: int = Field(ge=60)
    structure_window: int = Field(gt=1)
    volatility_window: int = Field(gt=1)
    high_volatility: float
    low_volatility: float
    trend_slope: float = Field(gt=0)


class Research(StrictConfig):
    forward_minutes: list[int]
    bucket_edges: list[float]
    train_months: int = Field(gt=0)
    validation_months: int = Field(gt=0)
    embargo_hours: int = Field(ge=24)


class Config(StrictConfig):
    market: Market
    trading: Trading
    strategy: Strategy
    risk: Risk
    simulation: Simulation
    jev: Jev
    features: Features
    research: Research

    @model_validator(mode="after")
    def coherent(self):
        from zoneinfo import ZoneInfo

        ZoneInfo(self.trading.timezone)
        if self.trading.base_currency not in {"KRW", "USD"}:
            raise ValueError("supported account currencies: KRW, USD")
        if self.trading.base_currency == "KRW" and (
            any(not s.endswith("/KRW") for s in self.market.crypto_symbols)
            or any(not (len(s) == 6 and s.isdigit()) for s in self.market.stock_symbols)
        ):
            raise ValueError(
                "KRW portfolios require KRW pairs and Korean six-digit stock/ETF codes"
            )
        if self.strategy.timeframe not in self.market.timeframes:
            raise ValueError("strategy timeframe must be collected")
        if set(self.market.timeframes) not in (
            {"5m", "15m", "1h"},
            {"1m", "5m", "15m", "1h"},
        ):
            raise ValueError("supports 1m decisions with 5m, 15m and 1h contexts")
        if self.market.execution_timeframe not in self.market.timeframes:
            raise ValueError("execution timeframe must be collected")
        if self.market.history_bars < self.features.minimum_bars:
            raise ValueError("insufficient history")
        edges = self.research.bucket_edges
        if edges != sorted(set(edges)) or edges[0] < 0 or edges[-1] > 1:
            raise ValueError("invalid probability bucket edges")
        if any(x <= 0 for x in self.research.forward_minutes):
            raise ValueError("forward horizons must be positive")
        if self.research.embargo_hours * 60 < max(self.research.forward_minutes):
            raise ValueError("embargo must cover the longest forward horizon")
        return self


def load_config() -> Config:
    path = Path(
        os.getenv("CONFIG_PATH", Path(__file__).resolve().parents[2] / "config.yaml")
    )
    return Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

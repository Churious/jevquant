from datetime import datetime, timedelta, timezone

import pytest

from app.config import load_config
from app.features import build_state, ema
from app.market import Bar, MINUTES, is_stale


def histories(flat=False):
    end = datetime(2026, 1, 10, tzinfo=timezone.utc)
    out = {}
    for tf, mins in MINUTES.items():
        out[tf] = [
            Bar(
                symbol="BTC/USDT",
                timeframe=tf,
                timestamp=end - timedelta(minutes=(100 - i) * mins),
                open=100 if flat else 100 + i,
                high=101 if flat else 101 + i,
                low=99 if flat else 99 + i,
                close=100 if flat else 100 + i,
                volume=10,
            )
            for i in range(100)
        ]
    return out, end


def test_features_flat_and_trend():
    h, now = histories(True)
    s = build_state("BTC/USDT", "15m", h, now, load_config())
    assert s["momentum"]["rsi14"] == 50
    assert s["volatility"]["atr14"] == 2
    assert s["price"]["return_24"] == 0
    assert s["volume"]["relative_volume"] == 1
    h, now = histories()
    s = build_state("BTC/USDT", "15m", h, now, load_config())
    assert s["momentum"]["rsi14"] == 100
    assert s["price"]["return_3"] == pytest.approx(199 / 196 - 1)
    assert s["trend"]["ema9"] > s["trend"]["ema21"] > s["trend"]["ema50"]
    assert list(ema([1, 2, 3], 3)) == [1, 1.5, 2.25]


def test_future_candles_cannot_change_state():
    h, now = histories()
    original = build_state("BTC/USDT", "15m", h, now, load_config())
    for tf in h:
        h[tf].append(
            Bar(
                symbol="BTC/USDT",
                timeframe=tf,
                timestamp=now,
                open=999,
                high=1000,
                low=998,
                close=999,
                volume=99,
            )
        )
    assert build_state("BTC/USDT", "15m", h, now, load_config()) == original


def test_stale_future_and_invalid_timestamps():
    h, now = histories()
    bar = h["15m"][-1]
    assert not is_stale(bar, now)
    assert is_stale(bar, now + timedelta(hours=1))
    assert is_stale(bar, now - timedelta(minutes=1))
    with pytest.raises(ValueError):
        Bar(
            symbol="X",
            timeframe="15m",
            timestamp=datetime(2026, 1, 1),
            open=1,
            high=2,
            low=1,
            close=1,
            volume=1,
        )


def test_gap_rejected():
    h, now = histories()
    h["5m"].pop(70)
    with pytest.raises(ValueError, match="DATA_GAP"):
        build_state("BTC/USDT", "15m", h, now, load_config())

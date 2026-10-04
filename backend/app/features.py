import math

import numpy as np

from .market import Bar, MINUTES, aware


def ema(values, period):
    result = [float(values[0])]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(alpha * float(value) + (1 - alpha) * result[-1])
    return np.array(result)


def wilder(values, period):
    result = float(np.mean(values[:period]))
    for value in values[period:]:
        result = (result * (period - 1) + float(value)) / period
    return result


def directional(bars, slope_threshold):
    closes = [b.close for b in bars]
    fast, slow = ema(closes, 9)[-1], ema(closes, 21)
    slope = slow[-1] / slow[-4] - 1
    if fast > slow[-1] and slope > 0:
        return ("strong_up" if slope > slope_threshold else "up"), float(slope)
    if fast < slow[-1] and slope < 0:
        return ("strong_down" if slope < -slope_threshold else "down"), float(slope)
    return "neutral", float(slope)


def build_state(
    symbol: str, timeframe: str, histories: dict[str, list[Bar]], asof, cfg
):
    # Exclude every candle whose close was not available at the decision time.
    histories = {
        tf: sorted(
            [b for b in bars if b.end <= aware(asof)], key=lambda b: b.timestamp
        )[-cfg.market.history_bars :]
        for tf, bars in histories.items()
    }
    for tf in cfg.market.timeframes:
        bars = histories.get(tf, [])
        if len(bars) < cfg.features.minimum_bars:
            raise ValueError(f"INSUFFICIENT_HISTORY:{tf}")
        if len({b.timestamp for b in bars}) != len(bars):
            raise ValueError("DUPLICATE_CANDLES")
        if any(b.symbol != symbol or b.timeframe != tf for b in bars):
            raise ValueError("MIXED_HISTORY")
        if (
            any(
                (b.timestamp - a.timestamp).total_seconds() != MINUTES[tf] * 60
                for a, b in zip(bars, bars[1:])
            )
            and "/" in symbol
        ):
            raise ValueError("DATA_GAP")
    bars = histories[timeframe]
    c = np.array([b.close for b in bars])
    h = np.array([b.high for b in bars])
    l = np.array([b.low for b in bars])
    v = np.array([b.volume for b in bars])
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    changes = np.diff(c)
    gain, loss = wilder(np.maximum(changes, 0), 14), wilder(np.maximum(-changes, 0), 14)
    rsi = (
        50.0
        if gain == loss == 0
        else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    )
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = wilder(tr, 14)
    macd = ema(c, 12) - ema(c, 26)
    signal = ema(macd, 9)
    window = cfg.features.volatility_window
    rv = float(np.std(np.diff(np.log(c))[-window:], ddof=1))
    recent_high = float(max(h[-cfg.features.structure_window :]))
    recent_low = float(min(l[-cfg.features.structure_window :]))
    volume_mean = float(np.mean(v[-20:]))
    trends = {
        tf: directional(b, cfg.features.trend_slope) for tf, b in histories.items()
    }
    if rv >= cfg.features.high_volatility:
        regime = "HIGH_VOLATILITY"
    elif rv <= cfg.features.low_volatility:
        regime = "LOW_VOLATILITY"
    elif trends[timeframe][0] in ("up", "strong_up"):
        regime = "TRENDING_UP"
    elif trends[timeframe][0] in ("down", "strong_down"):
        regime = "TRENDING_DOWN"
    else:
        regime = "RANGING"
    state = {
        "symbol": symbol,
        "timestamp": aware(asof).isoformat(),
        "timeframe": timeframe,
        "decision_context": {
            "execution_timeframe": cfg.market.execution_timeframe,
            "max_holding_minutes": cfg.strategy.max_holding_minutes,
            "estimated_round_trip_cost": 2
            * (cfg.simulation.fee_rate + cfg.simulation.slippage_bps / 10000),
            "return_period_unit_minutes": MINUTES[timeframe],
        },
        "units": {
            "returns": "fraction",
            "prices": "quote currency per unit",
            "volume": "provider units",
            "realized_volatility": "per-candle log-return stddev",
            "rsi": "0 to 100",
        },
        "price": {
            "current": float(c[-1]),
            **{f"return_{n}": float(c[-1] / c[-n - 1] - 1) for n in [1, 3, 12, 24]},
        },
        "trend": {
            "ema9": float(e9[-1]),
            "ema21": float(e21[-1]),
            "ema50": float(e50[-1]),
            "ema9_above_21": bool(e9[-1] > e21[-1]),
            "ema21_above_50": bool(e21[-1] > e50[-1]),
            "ema21_slope": float(e21[-1] / e21[-4] - 1),
            "price_vs_ema21": float(c[-1] / e21[-1] - 1),
            "price_vs_ema50": float(c[-1] / e50[-1] - 1),
        },
        "momentum": {
            "rsi14": rsi,
            "macd": float(macd[-1]),
            "macd_signal": float(signal[-1]),
            "macd_histogram": float(macd[-1] - signal[-1]),
            "roc": float(c[-1] / c[-13] - 1),
        },
        "volatility": {
            "atr14": atr,
            "atr_percent": atr / c[-1],
            "realized_volatility": rv,
            "bollinger_width": float(4 * np.std(c[-20:], ddof=0) / np.mean(c[-20:])),
        },
        "volume": {
            "current": float(v[-1]),
            "sma20": volume_mean,
            "relative_volume": float(v[-1] / volume_mean) if volume_mean > 0 else 0,
        },
        "market_structure": {
            "rolling_high": recent_high,
            "rolling_low": recent_low,
            "distance_recent_high": float(c[-1] / recent_high - 1),
            "distance_recent_low": float(c[-1] / recent_low - 1),
        },
        "higher_timeframe": {
            **{f"trend_{tf}": trend for tf, (trend, _) in trends.items()},
            "ema21_slope_1h": trends["1h"][1],
        },
        "regime": regime,
        "source_candle_timestamps": {
            tf: b[-1].timestamp.isoformat() for tf, b in histories.items()
        },
    }
    if cfg.tournament and cfg.tournament.enabled:
        # Keep legacy state hashes intact when replaying old saved configurations.
        mean, std = float(np.mean(c[-20:])), float(np.std(c[-20:], ddof=0))
        prior_high = float(max(h[-cfg.features.structure_window - 1 : -1]))
        prior_low = float(min(l[-cfg.features.structure_window - 1 : -1]))
        state["volatility"].update(
            {
                "bollinger_position": float((c[-1] - (mean - 2 * std)) / (4 * std))
                if std > 0
                else 0.5,
                "atr_deviation_ema21": float((c[-1] - e21[-1]) / atr) if atr > 0 else 0,
                "expansion_ratio": rv
                / max(
                    float(np.std(np.diff(np.log(c))[-2 * window : -window], ddof=1)),
                    1e-12,
                ),
            }
        )
        state["momentum"]["macd_histogram_change"] = float(
            (macd[-1] - signal[-1]) - (macd[-2] - signal[-2])
        )
        state["market_structure"].update(
            {
                "prior_rolling_high": prior_high,
                "prior_rolling_low": prior_low,
                "distance_prior_high": float(c[-1] / prior_high - 1),
                "distance_prior_low": float(c[-1] / prior_low - 1),
            }
        )
        state["higher_timeframe"]["structure"] = {
            tf: {
                "distance_prior_high": float(
                    b[-1].close / max(x.high for x in b[-25:-1]) - 1
                ),
                "distance_prior_low": float(
                    b[-1].close / min(x.low for x in b[-25:-1]) - 1
                ),
            }
            for tf, b in histories.items()
        }

    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        return not isinstance(value, float) or math.isfinite(value)

    if not finite(state):
        raise ValueError("NON_FINITE_FEATURE")
    return state


def compact_state(value, digits=6):
    """Bound model input size without dropping features; candle/ledger values stay exact."""
    if isinstance(value, dict):
        return {key: compact_state(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_state(item, digits) for item in value]
    return float(format(value, f".{digits}g")) if isinstance(value, float) else value

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.config import Config, load_config
from app.equity_sessions import context_fresh, session_open, session_exit_due
from app.market import Bar, FeatureSnapshot, store_bar, aware
from app.tournament import (
    ensure_tournament,
    start_tournament,
    finish_if_due,
    tournament_fingerprint,
    market_universe,
)
from app.trading import (
    Account,
    PaperBroker,
    PaperOrder,
    Position,
    ResearchRun,
    account_config,
    process_execution_batch,
)
from app.us_market import USStockProvider, aggregate_us
from app.research import state_hash
from app.traders import definition_fingerprint, runtime_for
from test_tournament import RUNTIME, signal

MONDAY = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
FRIDAY_CLOSE = datetime(2026, 10, 9, 20, tzinfo=timezone.utc)


def weekly_config():
    cfg = load_config().model_copy(deep=True)
    cfg.market.us_stock_symbols = ["AAPL", "MSFT", "SPY", "QQQ"]
    cfg.market.kr_holidays = ["2026-10-05", "2026-10-09"]
    cfg.trading.usd_krw = 1348.28
    cfg.trading.fx_reference = "2026-10-02 reference"
    cfg.trading.allow_us_fractional = True
    cfg.tournament.market_mode = "stock"
    cfg.tournament.duration_days = 5
    cfg.tournament.scheduled_start_at = MONDAY.isoformat()
    cfg.tournament.scheduled_end_at = FRIDAY_CLOSE.isoformat()
    cfg.tournament.start_market = "us"
    return Config.model_validate(cfg.model_dump())


def quote(when, price=300, tf="1m", symbol="AAPL"):
    return Bar(
        symbol=symbol,
        timeframe=tf,
        timestamp=when,
        open=price,
        high=price + 0.1,
        low=price - 0.1,
        close=price,
        volume=10,
    )


def test_regional_holidays_dst_and_exit_boundaries():
    cfg = weekly_config()
    assert not session_open("005930", MONDAY, cfg)
    assert not session_open(
        "005930", datetime(2026, 10, 9, 1, tzinfo=timezone.utc), cfg
    )
    assert session_open("005930", datetime(2026, 10, 6, 1, tzinfo=timezone.utc), cfg)
    assert not session_open("AAPL", MONDAY - timedelta(seconds=1), cfg)
    assert session_open("AAPL", MONDAY, cfg)
    assert session_open("AAPL", FRIDAY_CLOSE - timedelta(seconds=1), cfg)
    assert not session_open("AAPL", FRIDAY_CLOSE, cfg)
    assert not session_exit_due("AAPL", MONDAY, cfg)
    assert session_exit_due("AAPL", FRIDAY_CLOSE - timedelta(minutes=10), cfg)
    # November's open shifts by one UTC hour after daylight-saving time ends.
    assert not session_open(
        "AAPL", datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc), cfg
    )
    assert session_open("AAPL", datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc), cfg)


def test_open_uses_previous_session_context_but_never_old_execution_price():
    cfg = weekly_config()
    prior = quote(datetime(2026, 10, 2, 18, 30, tzinfo=timezone.utc), tf="1h")
    assert context_fresh(prior, MONDAY + timedelta(minutes=1), cfg)
    assert not context_fresh(prior, MONDAY + timedelta(minutes=61), cfg)
    assert not context_fresh(prior.model_copy(update={"timeframe": "1m"}), MONDAY, cfg)
    assert not context_fresh(
        prior.model_copy(update={"timestamp": prior.timestamp - timedelta(days=1)}),
        MONDAY,
        cfg,
    )


def test_fixed_week_cannot_start_early_or_extend_on_late_start(db):
    cfg = weekly_config()
    t = ensure_tournament(db, "weekly", cfg, RUNTIME)
    start_tournament(db, t, MONDAY - timedelta(seconds=1), cfg)
    assert t.status == "PENDING" and t.started_at is None
    start_tournament(db, t, MONDAY + timedelta(days=1), cfg)
    assert t.status == "RUNNING" and aware(t.ends_at) == FRIDAY_CLOSE
    assert len(market_universe(cfg)) == 8
    pending = ensure_tournament(db, "no-data", cfg, RUNTIME)
    start_tournament(db, pending, FRIDAY_CLOSE, cfg)
    assert pending.status == "PENDING"
    assert finish_if_due(db, pending, FRIDAY_CLOSE, cfg)
    assert pending.status == "EXPIRED" and pending.started_at is None
    assert db.get(ResearchRun, pending.id).paused
    assert not list(
        db.scalars(select(PaperOrder).join(Account).where(Account.run_id == pending.id))
    )


def test_us_native_quotes_convert_once_and_preserve_cash_final_valuation(db):
    cfg = weekly_config()
    t = ensure_tournament(db, "fx-ledger", cfg, RUNTIME)
    when = MONDAY + timedelta(hours=1, minutes=1)
    start_tournament(db, t, when, cfg)
    bar = quote(when)
    candle = store_bar(db, bar, "test-native-usd")
    state = {
        "symbol": "AAPL",
        "regime": "TRENDING_UP",
        "volatility": {"atr14": 2},
        "trend": {"ema9": 301, "ema21": 300, "ema9_above_21": True},
        "momentum": {"rsi14": 50},
        "higher_timeframe": {},
    }
    f = FeatureSnapshot(
        run_id=t.id,
        candle_id=candle.id,
        timestamp=when,
        state=state,
        config_hash=t.configuration_hash,
    )
    db.add(f)
    db.flush()
    definition = cfg.traders[0]
    decision = signal(db, cfg, t, definition, f, when)
    a = db.scalar(select(Account).where(Account.strategy == definition.id))
    positions = []
    assert (
        PaperBroker(account_config(db, a, cfg)).open(
            db, a, positions, decision, bar, state
        )
        == "EXECUTED"
    )
    p = positions[0]
    assert 0 < p.quantity < 1
    assert p.current_price == pytest.approx(300 * cfg.trading.usd_krw)
    order = db.get(PaperOrder, p.entry_order_id)
    assert order.fill_price * p.quantity <= 100000
    assert a.cash == pytest.approx(1000000 - order.fill_price * p.quantity - order.fees)
    # Quote storage and decision inputs retain USD; portfolio marks use KRW once.
    assert candle.close == 300 and f.state["volatility"]["atr14"] == 2
    process_execution_batch(db, t.id, [quote(when + timedelta(minutes=1), 301)], cfg)
    assert p.current_price == pytest.approx(301 * cfg.trading.usd_krw)
    last = quote(FRIDAY_CLOSE - timedelta(minutes=1), 310)
    store_bar(db, last, "test-native-usd")
    assert finish_if_due(db, t, FRIDAY_CLOSE, cfg)
    assert p.current_price == pytest.approx(310 * cfg.trading.usd_krw)
    assert t.final_report["valuation_prices"]["AAPL"]["quote_price"] == 310


def test_us_minute_aggregation_excludes_extended_incomplete_and_future_bars():
    cfg = weekly_config()
    rows = [quote(MONDAY + timedelta(minutes=i), 300 + i / 100) for i in range(61)]
    rows.append(quote(MONDAY - timedelta(minutes=1)))
    bars = aggregate_us(rows, "AAPL", "1h", MONDAY + timedelta(minutes=60))
    assert len(bars) == 1 and bars[0].timestamp == MONDAY and bars[0].volume == 600
    assert not aggregate_us(rows[1:], "AAPL", "1h", MONDAY + timedelta(minutes=60))
    assert not aggregate_us(rows, "AAPL", "1h", MONDAY + timedelta(minutes=59))
    final_partial = [quote(FRIDAY_CLOSE - timedelta(minutes=30 - i)) for i in range(30)]
    assert not aggregate_us(final_partial, "AAPL", "1h", FRIDAY_CLOSE)


async def test_us_credentials_separate_and_data_endpoint_only(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.setenv("KIWOOM_APP_KEY", "never-send-to-us")
    monkeypatch.setenv("STOCK_API_KEY", "never-send-to-us")
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET" and request.url.host == "data.alpaca.markets"
        assert (
            request.url.path == "/v2/stocks/bars"
            and request.url.params["feed"] == "iex"
        )
        assert request.headers["APCA-API-KEY-ID"] == "us-key"
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-10-02T14:30:00Z",
                            "o": 300,
                            "h": 301,
                            "l": 299,
                            "c": 300,
                            "v": 10,
                        }
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = USStockProvider(client)
        with pytest.raises(RuntimeError, match="CREDENTIALS"):
            await provider.history("AAPL", "1m", 200)
        assert not seen
        monkeypatch.setenv("ALPACA_API_KEY", "us-key")
        monkeypatch.setenv("ALPACA_API_SECRET", "us-secret")
        provider = USStockProvider(client)
        assert await asyncio.wait_for(provider.history("AAPL", "1m", 200), 0.5) == []
        await provider.jobs["AAPL"]
        assert len(seen) == 1 and len(provider.rows["AAPL"]) == 1
        await provider.close()


def test_new_optional_config_does_not_change_legacy_fingerprint():
    cfg = load_config()
    old = cfg.model_dump()
    for section, names in {
        "market": ["us_stock_symbols", "kr_holidays", "us_holidays"],
        "trading": ["usd_krw", "fx_reference", "allow_us_fractional"],
        "tournament": ["scheduled_start_at", "scheduled_end_at", "start_market"],
    }.items():
        for name in names:
            old[section].pop(name)
    expected = state_hash(
        {
            "configuration": old,
            "runtime": RUNTIME.identity(),
            "concurrency": RUNTIME.concurrency,
            "timeout": RUNTIME.timeout_seconds,
            "participants": {
                d.id: definition_fingerprint(d, runtime_for(d, RUNTIME))
                for d in cfg.traders
                if d.enabled
            },
        }
    )
    assert tournament_fingerprint(cfg, RUNTIME) == expected


def test_us_config_requires_fx_and_aware_schedule():
    cfg = weekly_config().model_dump()
    cfg["trading"]["usd_krw"] = None
    with pytest.raises(ValueError, match="USD/KRW"):
        Config.model_validate(cfg)
    cfg = weekly_config().model_dump()
    cfg["tournament"]["scheduled_start_at"] = "2026-10-05T09:30:00"
    with pytest.raises(ValueError, match="aware"):
        Config.model_validate(cfg)


def test_domestic_session_cannot_start_week_without_us_data(db, monkeypatch):
    from app.runner import Runner
    import app.runner as module

    cfg = weekly_config()
    t = ensure_tournament(db, "us-required", cfg, RUNTIME)
    now = datetime(2026, 10, 6, 1, 1, tzinfo=timezone.utc)
    histories = {
        tf: [
            quote(
                now - timedelta(minutes={"1m": 1, "5m": 5, "15m": 15, "1h": 60}[tf]),
                tf=tf,
                symbol="005930",
            )
        ]
        * 60
        for tf in cfg.market.timeframes
    }
    runner = SimpleNamespace(cfg=cfg, run_id=t.id, symbol_status={})

    class Scope:
        def __enter__(self):
            return db

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(module, "utcnow", lambda: now)
    monkeypatch.setattr(module, "Session", SimpleNamespace(begin=lambda: Scope()))
    assert Runner.prepare_tournament_symbol(runner, "005930", histories) == []
    assert runner.symbol_status["005930"] == "WAITING_START_MARKET"
    assert t.status == "PENDING" and t.started_at is None

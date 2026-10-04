import asyncio
import hashlib
import logging
import os
from datetime import timedelta

import httpx
from sqlalchemy import select

from .config import load_config
from .decision_runtime import DecisionRuntime, bind_runtime
from .db import Session, SystemEvent, utcnow
from .features import build_state, compact_state
from .market import Candle, FeatureSnapshot, MINUTES, is_stale, providers, store_bar
from .jev import JevClient, JevDecision
from .jev import StrategyDecision
from .trading import (
    STRATEGIES,
    MarketCursor,
    ensure_run,
    process_execution_batch,
    snapshot_accounts,
    strategy_action,
    Tournament,
)
from .market import aware
from .research import update_forward_returns
from .tournament import (
    ensure_tournament,
    finish_if_due,
    start_tournament,
    tournament_fingerprint,
    market_universe,
)
from .decision_queue import TournamentDecisionQueue

logger = logging.getLogger(__name__)


class Runner:
    def __init__(self):
        self.cfg = load_config()
        self.decision_runtime = DecisionRuntime.from_env()
        self.tournament_mode = bool(self.cfg.tournament and self.cfg.tournament.enabled)
        self.run_id = (
            os.getenv("LAB_TOURNAMENT_ID", "jev-tournament-7d-v1")
            if self.tournament_mode
            else os.getenv("LAB_RUN_ID", "krw-scalp-1m-v2")
        )
        self.config_hash = hashlib.sha256(
            self.cfg.model_dump_json().encode()
        ).hexdigest()
        if self.tournament_mode:
            self.config_hash = tournament_fingerprint(self.cfg, self.decision_runtime)
        self.lock = asyncio.Lock()
        self.tasks = []
        self.client = None
        self.status = "PAUSED"
        self.symbol_status = {}
        self.ws_status = "CONNECTING"
        self.history_cache = {}
        self.market_updated = asyncio.Event()
        self.jev_slots = asyncio.Semaphore(self.decision_runtime.concurrency)
        self.decision_queue = (
            TournamentDecisionQueue(self) if self.tournament_mode else None
        )

    def event(self, kind, payload=None, level="INFO"):
        with Session.begin() as db:
            db.add(SystemEvent(kind=kind, payload=payload or {}, level=level))

    async def start(self):
        self.client = httpx.AsyncClient(timeout=20)
        self.crypto, self.stock = providers(self.client)
        self.jev = JevClient(self.client, self.cfg, self.decision_runtime)
        with Session.begin() as db:
            if self.tournament_mode:
                ensure_tournament(db, self.run_id, self.cfg, self.decision_runtime)
            else:
                run = ensure_run(
                    db, self.run_id, "LIVE_PAPER", self.cfg, self.config_hash
                )
                bind_runtime(db, run, self.decision_runtime)
        if os.getenv("LAB_MODE", "LIVE_PAPER") != "LIVE_PAPER":
            self.status = "REPLAY_IDLE"
            return
        if os.getenv("LAB_AUTOSTART", "true").lower() != "true":
            return
        self.status = "RUNNING"
        self.tasks = [
            asyncio.create_task(self.supervise_poll()),
            asyncio.create_task(self.websocket()),
        ]
        if self.decision_queue:
            self.decision_queue.recover_interrupted()
            self.tasks.extend(
                asyncio.create_task(self.decision_queue.worker())
                for _ in range(self.decision_runtime.concurrency)
            )

    async def supervise_poll(self):
        while True:
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = "DATA_ERROR"
                self.event("WORKER_ERROR", {"error_type": type(exc).__name__}, "ERROR")
                await asyncio.sleep(self.cfg.market.poll_seconds)

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.client:
            if hasattr(self.stock, "close"):
                await self.stock.close()
            await self.client.aclose()

    async def websocket(self):
        if self.tournament_mode and self.cfg.tournament.market_mode == "stock":
            self.ws_status = "DISABLED"
            return
        while True:
            try:
                self.ws_status = "CONNECTED"
                await self.crypto.stream(
                    self.cfg.market.crypto_symbols
                    if not self.tournament_mode
                    or self.cfg.tournament.market_mode != "stock"
                    else [],
                    self.cfg.market.timeframes,
                    self.ingest_stream,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ws_status = "REST_FALLBACK"
                self.event("WS_FALLBACK", {"error_type": type(exc).__name__}, "WARNING")
                await asyncio.sleep(self.cfg.market.poll_seconds)

    async def ingest_stream(self, bar):
        if self.crypto.name == "upbit":
            # Streaming revisions are a completion signal. Persist the authoritative closed REST bar.
            completed = await self.crypto.history(bar.symbol, bar.timeframe, 2)
            bar = next((b for b in completed if b.timestamp == bar.timestamp), None)
            if bar is None:
                return
        async with self.lock:
            with Session.begin() as db:
                store_bar(db, bar, self.crypto.name)
        self.market_updated.set()
        # REST loop reconciles all contexts before each decision cycle.

    async def history(self, provider, symbol, tf):
        key = (provider.name, symbol, tf)
        cached = self.history_cache.get(key)
        if cached and cached[-1].end + timedelta(minutes=MINUTES[tf]) > utcnow():
            return cached, False
        limit = max(self.cfg.market.history_bars + 12, 300 if tf == "5m" else 0)
        bars = await provider.history(symbol, tf, min(limit, 1000))
        self.history_cache[key] = bars
        return bars, True

    async def process_symbol_safely(self, symbol, histories):
        try:
            await self.process_symbol(symbol, histories)
        except Exception as exc:
            logger.exception("Decision cycle failed for %s", symbol)
            self.symbol_status[symbol] = "DATA_ERROR"
            self.event(
                "CYCLE_ERROR",
                {"symbol": symbol, "error_type": type(exc).__name__},
                "ERROR",
            )

    @staticmethod
    def persist_history(db, bars, provider):
        canonical = []
        for bar in bars:
            try:
                candle = store_bar(db, bar, provider)
            except ValueError as exc:
                if not str(exc).startswith("CANDLE_CONFLICT:"):
                    raise
                candle = db.scalar(
                    select(Candle).where(
                        Candle.symbol == bar.symbol,
                        Candle.timeframe == bar.timeframe,
                        Candle.timestamp == bar.timestamp,
                    )
                )
                db.add(
                    SystemEvent(
                        kind="CANDLE_REVISION_IGNORED",
                        level="WARNING",
                        payload={
                            "provider": provider,
                            "observed_revision": bar.model_dump(mode="json"),
                            "retained_candle_id": candle.id,
                        },
                    )
                )
            canonical.append(candle.bar())
        return canonical

    async def poll(self):
        while True:
            started = asyncio.get_running_loop().time()
            self.market_updated.clear()
            collected = {}
            for provider, symbols in [
                (self.crypto, self.cfg.market.crypto_symbols),
                (self.stock, self.cfg.market.stock_symbols),
            ]:
                if self.tournament_mode:
                    symbols = [s for s in symbols if s in market_universe(self.cfg)]
                if not symbols:
                    continue
                if provider is self.stock and (
                    not self.stock.key or not self.stock.secret
                ):
                    for symbol in symbols:
                        self.symbol_status[symbol] = (
                            "KIWOOM_CREDENTIALS_MISSING"
                            if provider.name == "kiwoom"
                            else "CREDENTIALS_MISSING"
                        )
                    continue
                for symbol in symbols:
                    try:
                        histories = {}
                        refreshed = []
                        for tf in self.cfg.market.timeframes:
                            histories[tf], changed = await self.history(
                                provider, symbol, tf
                            )
                            if changed:
                                refreshed.append(tf)
                        async with self.lock:
                            with Session.begin() as db:
                                for tf in refreshed:
                                    histories[tf] = self.persist_history(
                                        db, histories[tf], provider.name
                                    )
                            for tf in refreshed:
                                self.history_cache[(provider.name, symbol, tf)] = (
                                    histories[tf]
                                )
                        collected[symbol] = histories
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        for tf in self.cfg.market.timeframes:
                            self.history_cache.pop((provider.name, symbol, tf), None)
                        self.symbol_status[symbol] = "DATA_ERROR"
                        self.event(
                            "DATA_ERROR",
                            {"symbol": symbol, "error_type": type(exc).__name__},
                            "ERROR",
                        )
            async with self.lock:
                batches = {}
                with Session.begin() as db:
                    tournament = (
                        db.get(Tournament, self.run_id)
                        if self.tournament_mode
                        else None
                    )
                    for symbol, histories in collected.items():
                        cursor = db.scalar(
                            select(MarketCursor).where(
                                MarketCursor.run_id == self.run_id,
                                MarketCursor.symbol == symbol,
                            )
                        )
                        bars = histories.get(self.cfg.market.execution_timeframe, [])
                        if bars and cursor:
                            for bar in bars:
                                if bar.timestamp > aware(cursor.timestamp):
                                    batches.setdefault(bar.timestamp, []).append(bar)
                                    cursor.timestamp = bar.timestamp
                        elif bars:
                            db.add(
                                MarketCursor(
                                    run_id=self.run_id,
                                    symbol=symbol,
                                    timestamp=bars[-1].timestamp,
                                )
                            )
                    for timestamp, bars in sorted(batches.items()):
                        if tournament:
                            bars = [
                                b
                                for b in bars
                                if tournament.started_at
                                and b.timestamp >= aware(tournament.started_at)
                                and b.end <= aware(tournament.ends_at)
                                and tournament.status in {"RUNNING", "PAUSED"}
                            ]
                            if not bars:
                                continue
                        process_execution_batch(
                            db,
                            self.run_id,
                            bars,
                            self.cfg,
                            allow_entries=all(
                                not is_stale(
                                    b, utcnow(), self.cfg.market.stale_multiplier
                                )
                                for b in bars
                            ),
                        )
                    if not batches and not tournament:
                        snapshot_accounts(db, self.run_id, utcnow(), self.cfg)
                    if tournament:
                        finish_if_due(db, tournament, utcnow(), self.cfg)
                    update_forward_returns(db, self.run_id, utcnow(), self.cfg)
                if self.tournament_mode:
                    jobs = []
                    for symbol, histories in collected.items():
                        try:
                            jobs.extend(
                                self.prepare_tournament_symbol(symbol, histories)
                            )
                        except ValueError:
                            self.symbol_status[symbol] = "WARMING_UP"
                    await self.decision_queue.submit(
                        jobs, int(utcnow().timestamp() // 60)
                    )
                else:
                    await asyncio.gather(
                        *(
                            self.process_symbol_safely(symbol, histories)
                            for symbol, histories in collected.items()
                        )
                    )
            remaining = max(
                0.05,
                self.cfg.market.poll_seconds
                - (asyncio.get_running_loop().time() - started),
            )
            try:
                await asyncio.wait_for(self.market_updated.wait(), timeout=remaining)
            except TimeoutError:
                pass

    def prepare_tournament_symbol(self, symbol, histories):
        now = utcnow()
        if any(
            len(histories.get(tf, [])) < self.cfg.features.minimum_bars
            for tf in self.cfg.market.timeframes
        ):
            self.symbol_status[symbol] = "WARMING_UP"
            return []
        if any(
            is_stale(
                b[-1],
                now,
                self.cfg.market.stale_multiplier,
                self.cfg.market.future_tolerance_seconds,
            )
            for b in histories.values()
        ):
            self.symbol_status[symbol] = "DATA_STALE"
            return []
        if "/" not in symbol:
            from zoneinfo import ZoneInfo

            local = now.astimezone(ZoneInfo(self.cfg.trading.timezone))
            if local.weekday() >= 5 or not 540 <= local.hour * 60 + local.minute < 930:
                self.symbol_status[symbol] = "MARKET_CLOSED"
                return []
        bar = histories[self.cfg.strategy.timeframe][-1]
        with Session.begin() as db:
            tournament = db.get(Tournament, self.run_id)
            if tournament.status not in {"PENDING", "RUNNING"}:
                return []
            start_tournament(db, tournament, now, self.cfg)
            state = build_state(
                symbol, self.cfg.strategy.timeframe, histories, bar.end, self.cfg
            )
            if self.cfg.tournament.state_precision_digits:
                state = compact_state(state, self.cfg.tournament.state_precision_digits)
            candle = db.scalar(
                select(Candle).where(
                    Candle.symbol == symbol,
                    Candle.timeframe == bar.timeframe,
                    Candle.timestamp == bar.timestamp,
                )
            )
            feature = db.scalar(
                select(FeatureSnapshot).where(
                    FeatureSnapshot.run_id == self.run_id,
                    FeatureSnapshot.candle_id == candle.id,
                )
            )
            if not feature:
                feature = FeatureSnapshot(
                    run_id=self.run_id,
                    candle_id=candle.id,
                    timestamp=bar.end,
                    state=state,
                    config_hash=self.config_hash,
                )
                db.add(feature)
                db.flush()
            jobs = self.decision_queue.prepare(db, feature, tournament, now)
            self.symbol_status[symbol] = "RUNNING"
            return jobs

    async def process_symbol(self, symbol, histories):
        now = utcnow()
        tf = self.cfg.strategy.timeframe
        if any(
            len(bars) < self.cfg.features.minimum_bars for bars in histories.values()
        ):
            self.symbol_status[symbol] = "WARMING_UP"
            return
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(self.cfg.trading.timezone))
        if "/" not in symbol and (
            local.weekday() >= 5 or not 540 <= local.hour * 60 + local.minute < 930
        ):
            self.symbol_status[symbol] = "MARKET_CLOSED"
            return
        if any(
            not bars
            or is_stale(
                bars[-1],
                now,
                self.cfg.market.stale_multiplier,
                self.cfg.market.future_tolerance_seconds,
            )
            for bars in histories.values()
        ):
            self.symbol_status[symbol] = "DATA_STALE"
            return
        bar = histories[tf][-1]
        state = build_state(symbol, tf, histories, bar.end, self.cfg)
        with Session.begin() as db:
            candle = db.scalar(
                select(Candle).where(
                    Candle.symbol == symbol,
                    Candle.timeframe == tf,
                    Candle.timestamp == bar.timestamp,
                )
            )
            old = db.scalar(
                select(FeatureSnapshot).where(
                    FeatureSnapshot.run_id == self.run_id,
                    FeatureSnapshot.candle_id == candle.id,
                )
            )
            if not old:
                old = FeatureSnapshot(
                    run_id=self.run_id,
                    candle_id=candle.id,
                    timestamp=bar.end,
                    state=state,
                    config_hash=self.config_hash,
                )
                db.add(old)
                db.flush()
            feature_id = old.id
            state = old.state
            decided = db.scalar(
                select(JevDecision).where(JevDecision.feature_id == feature_id)
            )
        if not decided:
            async with self.jev_slots:
                result = await self.jev.evaluate(state)
            with Session.begin() as db:
                db.add(JevDecision(feature_id=feature_id, **result))
            self.symbol_status[symbol] = (
                "RUNNING" if result["status"] == "OK" else "JEV_UNAVAILABLE"
            )
        else:
            self.symbol_status[symbol] = (
                "RUNNING" if decided.status == "OK" else "JEV_UNAVAILABLE"
            )
        with Session.begin() as db:
            decided = db.scalar(
                select(JevDecision).where(JevDecision.feature_id == feature_id)
            )
            if not db.scalar(
                select(StrategyDecision.id).where(
                    StrategyDecision.feature_id == feature_id
                )
            ):
                for name in STRATEGIES:
                    action, reason = strategy_action(name, state, decided, self.cfg)
                    db.add(
                        StrategyDecision(
                            run_id=self.run_id,
                            feature_id=feature_id,
                            jev_decision_id=decided.id if name == "jev" else None,
                            timestamp=utcnow(),
                            strategy=name,
                            strategy_version=self.cfg.strategy.version
                            if name == "jev"
                            else name + "-v1",
                            action=action,
                            reason=reason,
                        )
                    )


runner = Runner()

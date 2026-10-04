import asyncio
from datetime import timedelta

from sqlalchemy import select

from .db import Session, utcnow
from .jev import JevClient, JevDecision, StrategyDecision
from .market import FeatureSnapshot, aware
from .trading import Tournament
from .traders import effective_config, questions_for, runtime_for, trader_action


def record_action(db, feature, definition, decision, cfg, observed_at):
    existing = db.scalar(
        select(StrategyDecision.id).where(
            StrategyDecision.feature_id == feature.id,
            StrategyDecision.strategy == definition.id,
        )
    )
    if existing:
        return
    action, reason = trader_action(definition, feature.state, decision, cfg)
    db.add(
        StrategyDecision(
            run_id=feature.run_id,
            tournament_id=feature.run_id,
            trader_id=definition.id,
            feature_id=feature.id,
            jev_decision_id=decision.id if decision else None,
            timestamp=observed_at,
            strategy=definition.id,
            strategy_version=effective_config(cfg, definition).strategy.version,
            action=action,
            reason=reason,
        )
    )


class TournamentDecisionQueue:
    def __init__(self, runner):
        self.runner = runner
        self.queue = asyncio.Queue(maxsize=runner.cfg.tournament.max_queued_jobs)
        self.definitions = {d.id: d for d in runner.cfg.traders if d.enabled}

    def prepare(self, db, feature, tournament, now):
        jobs = []
        for definition in self.definitions.values():
            if definition.type == "baseline":
                record_action(db, feature, definition, None, self.runner.cfg, now)
                continue
            existing = db.scalar(
                select(JevDecision.id).where(
                    JevDecision.feature_id == feature.id,
                    JevDecision.trader_id == definition.id,
                )
            )
            if existing:
                continue
            runtime = runtime_for(definition, self.runner.decision_runtime)
            deadline = min(
                aware(feature.timestamp) + timedelta(minutes=1),
                aware(now)
                + timedelta(
                    seconds=self.runner.cfg.tournament.decision_cycle_budget_seconds
                ),
                aware(tournament.ends_at),
            )
            decision = JevDecision(
                feature_id=feature.id,
                trader_id=definition.id,
                tournament_id=tournament.id,
                timestamp=now,
                queued_at=now,
                deadline_at=deadline,
                status="QUEUED",
                request={
                    "model": runtime.model,
                    "state": feature.state,
                    "questions": questions_for(definition),
                },
                raw_response=None,
                attempts=[],
                latency_ms=0,
                request_count=0,
                estimated_cost=None,
            )
            db.add(decision)
            db.flush()
            jobs.append((definition.id, feature.state["symbol"], decision.id))
        return jobs

    async def submit(self, jobs, minute):
        names = [d.id for d in self.definitions.values() if d.type == "jev"]
        offset = minute % len(names) if names else 0
        rotated = names[offset:] + names[:offset]
        # Rotate both style and asset priority so queue misses are visible and distributed.
        jobs.sort(key=lambda j: (rotated.index(j[0]), j[1]), reverse=False)
        if minute % 2:
            jobs = sorted(jobs, key=lambda j: rotated.index(j[0]))
            groups = []
            for name in rotated:
                groups.extend(reversed([j for j in jobs if j[0] == name]))
            jobs = groups
        for _, _, decision_id in jobs:
            try:
                self.queue.put_nowait(decision_id)
            except asyncio.QueueFull:
                self.complete(decision_id, None, "QUEUE_FULL", utcnow())

    def recover_interrupted(self):
        with Session() as db:
            ids = list(
                db.scalars(
                    select(JevDecision.id).where(
                        JevDecision.tournament_id == self.runner.run_id,
                        JevDecision.status.in_(["QUEUED", "PROCESSING"]),
                    )
                )
            )
        for decision_id in ids:
            self.complete(decision_id, None, "INTERRUPTED", utcnow())

    def complete(self, decision_id, result, status, completed_at):
        with Session.begin() as db:
            decision = db.get(JevDecision, decision_id)
            feature = db.get(FeatureSnapshot, decision.feature_id)
            if result:
                for key, value in result.items():
                    setattr(decision, key, value)
            tournament = db.get(Tournament, decision.tournament_id)
            if tournament.status == "COMPLETED" or aware(completed_at) >= aware(
                tournament.ends_at
            ):
                status = "TOURNAMENT_ENDED"
            decision.status = status
            decision.timestamp = completed_at
            decision.decision_completed_at = completed_at
            if decision.decision_started_at:
                decision.latency_ms = max(
                    0,
                    (
                        aware(completed_at) - aware(decision.decision_started_at)
                    ).total_seconds()
                    * 1000,
                )
            record_action(
                db,
                feature,
                self.definitions[decision.trader_id],
                decision,
                self.runner.cfg,
                completed_at,
            )

    async def worker(self):
        while True:
            decision_id = await self.queue.get()
            try:
                with Session.begin() as db:
                    decision = db.get(JevDecision, decision_id)
                    tournament = db.get(Tournament, decision.tournament_id)
                    now = utcnow()
                    definition = self.definitions[decision.trader_id]
                    state = decision.request["state"]
                    remaining = (aware(decision.deadline_at) - now).total_seconds()
                    available = tournament.status == "RUNNING" and now < aware(
                        tournament.ends_at
                    )
                    if available and remaining > 0:
                        decision.status = "PROCESSING"
                        decision.decision_started_at = now
                if not available or remaining <= 0:
                    self.complete(
                        decision_id,
                        None,
                        "BUDGET_EXPIRED" if available else "TOURNAMENT_INACTIVE",
                        utcnow(),
                    )
                    continue
                cfg = effective_config(self.runner.cfg, definition)
                cfg.jev.max_evaluation_seconds = min(
                    cfg.jev.max_evaluation_seconds, remaining
                )
                client = JevClient(
                    self.runner.client,
                    cfg,
                    runtime_for(definition, self.runner.decision_runtime),
                )
                result = await client.evaluate(state, questions_for(definition))
                completed = utcnow()
                with Session() as db:
                    decision = db.get(JevDecision, decision_id)
                    tournament = db.get(Tournament, decision.tournament_id)
                    late = (
                        completed >= aware(decision.deadline_at)
                        or completed >= aware(tournament.ends_at)
                        or tournament.status != "RUNNING"
                    )
                async with self.runner.lock:
                    self.complete(
                        decision_id,
                        result,
                        "LATE_DECISION" if late else result["status"],
                        completed,
                    )
            except asyncio.CancelledError:
                self.complete(decision_id, None, "INTERRUPTED", utcnow())
                raise
            except Exception as exc:
                self.complete(decision_id, None, "WORKER_ERROR", utcnow())
                self.runner.event(
                    "DECISION_WORKER_ERROR",
                    {"decision_id": decision_id, "error_type": type(exc).__name__},
                    "ERROR",
                )
            finally:
                self.queue.task_done()

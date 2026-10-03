"""Local Docker/SSH administration only; the HTTP dashboard has no write routes."""

import argparse
import os

from sqlalchemy import select

from .db import Session, SystemEvent
from .jev import StrategyDecision
from .trading import ResearchRun


def main():
    parser = argparse.ArgumentParser(description="로컬 가상거래 관리")
    parser.add_argument("action", choices=["pause", "resume"])
    parser.add_argument("--run-id", default=os.getenv("LAB_RUN_ID", "krw-scalp-1m-v2"))
    args = parser.parse_args()
    with Session.begin() as db:
        run = db.get(ResearchRun, args.run_id)
        if not run:
            parser.error("실험을 찾을 수 없습니다")
        run.paused = args.action == "pause"
        if run.paused:
            for s in db.scalars(
                select(StrategyDecision).where(
                    StrategyDecision.run_id == args.run_id,
                    StrategyDecision.status == "PENDING",
                    StrategyDecision.action.in_(["LONG", "SHORT"]),
                )
            ):
                s.status = "PAUSED"
        db.add(
            SystemEvent(
                kind="LOCAL_ADMIN",
                payload={"action": args.action, "run_id": args.run_id},
            )
        )
    print("가상거래 일시정지" if run.paused else "가상거래 재개")


if __name__ == "__main__":
    main()

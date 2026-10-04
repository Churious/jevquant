import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, ForeignKey, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

engine = create_engine(
    os.getenv("DATABASE_URL", "sqlite:///./data/lab.db"), pool_pre_ping=True
)
Session = sessionmaker(engine, expire_on_commit=False)
json_type = JSON().with_variant(JSONB, "postgresql")


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ExperimentScope:
    tournament_id: Mapped[str | None] = mapped_column(
        ForeignKey("tournaments.id"), nullable=True, index=True
    )
    trader_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(json_type, default=dict)


def init_db():
    Base.metadata.create_all(engine)
    from .migrations import migrate

    migrate(engine)

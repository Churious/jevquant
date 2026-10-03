"""Server-only selection of a hosted or local System One decision endpoint."""

import os
import math
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select


@dataclass(frozen=True)
class DecisionRuntime:
    provider: str
    base_url: str
    model: str
    concurrency: int
    timeout_seconds: float | None = None

    @classmethod
    def from_env(cls):
        provider = os.getenv("JEV_PROVIDER", "typesafe").strip().lower()
        if provider not in {"typesafe", "local"}:
            raise ValueError("JEV_PROVIDER must be typesafe or local")
        local = provider == "local"
        base = os.getenv("JEV_BASE_URL", "").strip().rstrip("/") or (
            "http://localhost:11434" if local else "https://api.typesafe.ai"
        )
        parts = urlsplit(base)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "JEV_BASE_URL must be an HTTP(S) base URL without credentials, query or fragment"
            )
        if base.endswith("/v1/systemone"):
            base = base.removesuffix("/v1/systemone")
        elif base.endswith("/v1"):
            base = base.removesuffix("/v1")
        model = os.getenv("JEV_MODEL", "").strip() or (
            "tev1:0.8b" if local else "jev-1.13.0"
        )
        if len(model) > 64 or any(c.isspace() for c in model):
            raise ValueError("JEV_MODEL must be a model name of at most 64 characters")
        concurrency = int(
            os.getenv("JEV_CONCURRENCY", "").strip() or ("1" if local else "4")
        )
        if not 1 <= concurrency <= 8:
            raise ValueError("JEV_CONCURRENCY must be between 1 and 8")
        timeout = (
            float(os.getenv("LOCAL_JEV_TIMEOUT_SECONDS", "").strip() or "7")
            if local
            else None
        )
        if timeout is not None and (
            not math.isfinite(timeout) or not 0 < timeout <= 60
        ):
            raise ValueError(
                "LOCAL_JEV_TIMEOUT_SECONDS must be between 0 and 60 seconds"
            )
        return cls(provider, base, model, concurrency, timeout)

    @property
    def endpoint(self):
        return self.base_url + "/v1/systemone"

    @property
    def api_key(self):
        variable = (
            "LOCAL_JEV_API_KEY" if self.provider == "local" else "TYPESAFE_API_KEY"
        )
        return os.getenv(variable, "").strip()

    def identity(self):
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
        }

    def model_version(self, returned_model):
        version = (
            f"local:{returned_model}" if self.provider == "local" else returned_model
        )
        if len(version) > 80:
            raise ValueError("response model identifier is too long")
        return version


def bind_runtime(db, run, runtime):
    """Do not mix endpoint/model changes into an existing paper experiment."""
    from .jev import JevDecision
    from .market import FeatureSnapshot

    previous = (run.metadata_json or {}).get("decision_runtime")
    if previous is None:
        legacy = db.scalar(
            select(JevDecision)
            .join(FeatureSnapshot)
            .where(FeatureSnapshot.run_id == run.id)
            .order_by(JevDecision.id)
            .limit(1)
        )
        if legacy:
            previous = {
                "provider": "typesafe",
                "base_url": "https://api.typesafe.ai",
                "model": legacy.request["model"],
            }
    if previous is not None and previous != runtime.identity():
        raise ValueError(
            "DECISION_RUNTIME_CHANGED: set a new LAB_RUN_ID when changing provider, endpoint or model"
        )
    run.metadata_json = {
        **(run.metadata_json or {}),
        "decision_runtime": runtime.identity(),
    }

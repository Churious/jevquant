import asyncio
import math
import time
import json

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, json_type, utcnow
from .decision_runtime import DecisionRuntime

TREND = ["strong_down", "down", "neutral", "up", "strong_up"]
SCALES = {
    "momentum_quality": ["very_weak", "weak", "neutral", "strong", "very_strong"],
    "setup_quality": ["poor", "weak", "average", "good", "excellent"],
    "reversal_risk": ["very_low", "low", "medium", "high", "very_high"],
    "market_noise": ["very_low", "low", "medium", "high", "very_high"],
}

QUESTIONS = {
    "trend_direction": {
        "type": "choice",
        "instructions": "Based only on the supplied market state, classify the current directional trend.",
        "criteria": {
            "strong_down": "Sustained negative EMA alignment and slope",
            "down": "Moderately bearish alignment",
            "neutral": "No coherent directional trend",
            "up": "Moderately bullish alignment",
            "strong_up": "Sustained positive EMA alignment and slope",
        },
    },
    "momentum_quality": {
        "type": "score",
        "instructions": "Rate how supportive current momentum is for continuation in the prevailing direction.",
        "criteria": [
            "Very weak: momentum opposes continuation",
            "Weak: little support",
            "Neutral: mixed evidence",
            "Strong: RSI and MACD support continuation",
            "Very strong: coherent persistent momentum",
        ],
    },
    "setup_quality": {
        "type": "score",
        "instructions": "Rate the quality of the current market setup for a short-term directional trade.",
        "criteria": [
            "Poor: incoherent evidence",
            "Weak: conflicting evidence",
            "Average: incomplete alignment",
            "Good: coherent trend, momentum and context",
            "Excellent: exceptionally coherent evidence",
        ],
    },
    "reversal_risk": {
        "type": "score",
        "instructions": "Estimate the immediate reversal risk given trend, momentum, volatility and market structure.",
        "criteria": [
            "Very low: stable alignment",
            "Low: few reversal signs",
            "Medium: some conflicting evidence",
            "High: overextension or divergence",
            "Very high: strong reversal evidence",
        ],
    },
    "market_noise": {
        "type": "score",
        "instructions": "Estimate how noisy or directionless the current market conditions are.",
        "criteria": [
            "Very low: clear direction",
            "Low: mostly directional",
            "Medium: mixed direction",
            "High: inconsistent direction",
            "Very high: chaotic or directionless",
        ],
    },
    "avoid_trade": {
        "type": "noul",
        "instructions": "Should a directional trade be avoided because the current evidence is ambiguous, conflicting, excessively volatile or low quality?",
    },
    "long_setup": {
        "type": "noul",
        "instructions": "Does the supplied market state represent a sufficiently coherent bullish setup for consideration by a deterministic trading strategy?",
    },
    "short_setup": {
        "type": "noul",
        "instructions": "Does the supplied market state represent a sufficiently coherent bearish setup for consideration by a deterministic trading strategy?",
    },
}


class Answer(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    type: str
    choice: str | None = None
    score: float | None = None
    noul: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] | None = None
    legend: dict[str, str] | None = None


class JevResponse(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    model: str = Field(min_length=1)
    answers: dict[str, Answer]
    usage: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_answers(self):
        for key, question in QUESTIONS.items():
            answer = self.answers.get(key)
            if answer is None or answer.type != question["type"]:
                raise ValueError(f"missing or mismatched answer:{key}")
            if answer.type == "noul":
                if answer.noul is None:
                    raise ValueError("missing noul")
                continue
            probabilities = answer.probabilities
            if (
                answer.confidence is None
                or not probabilities
                or any(
                    not math.isfinite(p) or p < 0 or p > 1
                    for p in probabilities.values()
                )
            ):
                raise ValueError("invalid probabilities/confidence")
            if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.01):
                raise ValueError("probabilities must sum to 1")
            expected = set(
                TREND if answer.type == "choice" else [str(i) for i in range(5)]
            )
            if set(probabilities) != expected:
                raise ValueError("unexpected probability options")
            if answer.type == "choice" and answer.choice not in expected:
                raise ValueError("invalid choice")
            if answer.type == "choice" and probabilities[answer.choice] < max(
                probabilities.values()
            ):
                raise ValueError("choice disagrees with probabilities")
            if answer.type == "score":
                if (
                    answer.score is None
                    or not 0 <= answer.score <= 4
                    or not answer.legend
                    or set(answer.legend) != expected
                ):
                    raise ValueError("invalid score/legend")
                if not math.isclose(
                    answer.score,
                    sum(int(k) * v for k, v in probabilities.items()),
                    abs_tol=0.02,
                ):
                    raise ValueError("score disagrees with probabilities")
        tokens = self.usage.get("input_tokens")
        if tokens is not None and (
            isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0
        ):
            raise ValueError("invalid token usage")
        return self


class JevDecision(Base):
    __tablename__ = "jev_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id"), unique=True)
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)
    request: Mapped[dict] = mapped_column(json_type)
    raw_response: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    attempts: Mapped[list] = mapped_column(json_type, default=list)
    model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    latency_ms: Mapped[float] = mapped_column(Float)
    request_count: Mapped[int] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)


class StrategyDecision(Base):
    __tablename__ = "strategy_decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id"))
    jev_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("jev_decisions.id"), nullable=True
    )
    timestamp: Mapped[object] = mapped_column(DateTime(timezone=True))
    strategy: Mapped[str] = mapped_column(String(32))
    strategy_version: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="PENDING")


class JevClient:
    def __init__(self, client: httpx.AsyncClient, cfg, runtime=None):
        self.client, self.cfg = client, cfg
        self.runtime = runtime or DecisionRuntime.from_env()
        self.request_timeout = self.runtime.timeout_seconds or cfg.jev.timeout_seconds

    async def evaluate(self, state):
        request = {
            "model": self.runtime.model,
            "state": state,
            "questions": QUESTIONS,
        }
        key = self.runtime.api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        started = time.monotonic()
        attempts = []
        raw = None
        status = "JEV_UNAVAILABLE"
        parsed = None
        model_version = None
        if key or self.runtime.provider == "local":
            for n in range(self.cfg.jev.attempts):
                remaining = self.cfg.jev.max_evaluation_seconds - (
                    time.monotonic() - started
                )
                if remaining <= 0:
                    break
                retry = False
                wait = self.cfg.jev.backoff_seconds * 2**n
                try:
                    response = await asyncio.wait_for(
                        self.client.post(
                            self.runtime.endpoint,
                            headers=headers,
                            json=request,
                            timeout=min(self.request_timeout, remaining),
                        ),
                        timeout=min(self.request_timeout, remaining),
                    )
                    try:

                        def reject_constant(value):
                            raise ValueError("non-finite JSON constant")

                        body = json.loads(response.text, parse_constant=reject_constant)
                        raw = body if isinstance(body, dict) else {"body": body}
                    except ValueError:
                        raw = {"error": "NON_JSON_RESPONSE", "body": response.text}
                    attempts.append(
                        {
                            "status_code": response.status_code,
                            "raw_response": raw,
                            "runtime": self.runtime.identity(),
                        }
                    )
                    retry = response.status_code in {429, 500, 502, 503, 504, 529}
                    if retry:
                        header = response.headers.get("retry-after", "")
                        if header.isdigit():
                            wait = min(max(wait, float(header)), 60)
                    response.raise_for_status()
                    parsed = JevResponse.model_validate(raw)
                    model_version = self.runtime.model_version(parsed.model)
                    status = "OK"
                    break
                except httpx.HTTPStatusError:
                    pass
                except (httpx.TransportError, TimeoutError) as exc:
                    attempts.append(
                        {
                            "error_type": type(exc).__name__,
                            "runtime": self.runtime.identity(),
                        }
                    )
                    retry = True
                except ValueError:
                    status = "INVALID_RESPONSE"
                    break
                if (
                    not retry
                    or n + 1 >= self.cfg.jev.attempts
                    or time.monotonic() - started + wait
                    >= self.cfg.jev.max_evaluation_seconds
                ):
                    break
                await asyncio.sleep(wait)
        cost = None
        token_counts = [
            (a.get("raw_response", {}).get("usage") or {}).get("input_tokens")
            if isinstance(a.get("raw_response", {}).get("usage"), dict)
            else None
            for a in attempts
        ]
        if self.runtime.provider == "local" and attempts:
            cost = (
                0.0  # API billing only; electricity/hardware costs are not estimated.
            )
        elif token_counts and all(
            isinstance(t, int) and not isinstance(t, bool) and t >= 0
            for t in token_counts
        ):
            cost = sum(token_counts) / 1_000_000 * self.cfg.jev.input_cost_per_million
        return {
            "request": request,
            "raw_response": raw,
            "attempts": attempts,
            "model_version": model_version,
            "status": status,
            "latency_ms": (time.monotonic() - started) * 1000,
            "request_count": len(attempts),
            "estimated_cost": cost,
        }

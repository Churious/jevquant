"""Declarative trader catalog; the paper engine only sees IDs and frozen configs."""

from dataclasses import replace

from .jev import JevResponse
from .research import state_hash

DIRECTIONS = {
    "strong_down": "Strong bearish opportunity",
    "down": "Bearish opportunity",
    "neutral": "No directional opportunity",
    "up": "Bullish opportunity",
    "strong_up": "Strong bullish opportunity",
}
REGIMES = {
    "TREND": "Persistent directional trend",
    "RANGE": "Range-bound oscillation",
    "BREAKOUT": "Confirmed escape from recent range",
    "REVERSAL": "Evidence of a turning point",
    "HIGH_VOLATILITY": "Unstable, excessive volatility",
    "UNCERTAIN": "Insufficient or conflicting evidence",
}


def choice(instructions, options=None):
    return {
        "type": "choice",
        "instructions": instructions,
        "criteria": options or DIRECTIONS,
    }


def score(instructions):
    return {
        "type": "score",
        "instructions": instructions,
        "criteria": [
            "Absent / very low",
            "Weak / low",
            "Moderate / mixed",
            "Strong / high",
            "Very strong / very high",
        ],
    }


CATALOG = {
    "trend": {
        "trend_direction": choice(
            "Classify trend opportunity using EMA9/21/50, slopes, price structure, volume and higher timeframes."
        ),
        "trend_strength": score(
            "Rate the strength and persistence of the directional trend."
        ),
        "trend_continuation_quality": score(
            "Rate trend continuation quality; range-bound noise is poor quality."
        ),
        "reversal_risk": score("Rate immediate reversal risk or trend overextension."),
    },
    "momentum": {
        "momentum_direction": choice(
            "Classify short-term momentum acceleration using RSI, MACD histogram changes, ROC and recent returns."
        ),
        "momentum_strength": score(
            "Rate momentum acceleration strength, supported by relative volume."
        ),
        "momentum_sustainability": score(
            "Rate whether short-term momentum can persist after trading costs."
        ),
        "exhaustion_risk": score("Rate the risk of exhausted momentum or divergence."),
    },
    "breakout": {
        "breakout_direction": choice(
            "Classify escape direction relative to PRIOR rolling high/low; require structure and relative-volume evidence."
        ),
        "breakout_quality": score(
            "Rate breakout quality using prior range, ATR and volatility expansion."
        ),
        "breakout_confirmation": score(
            "Rate confirmation of the breakout by volume and higher-timeframe structure."
        ),
        "false_breakout_risk": score(
            "Rate fakeout risk; an unconfirmed near-boundary price is high risk."
        ),
    },
    "mean_reversion": {
        "overextension": score(
            "Rate short-term overextension using RSI, Bollinger position, EMA distance and ATR deviation."
        ),
        "reversion_direction": choice(
            "Classify the expected mean-reversion direction, not the direction of the preceding move."
        ),
        "reversion_quality": score(
            "Rate evidence for a reversal toward the mean after costs."
        ),
        "trend_against_trade_risk": score(
            "Rate risk of opposing a strong sustained trend; high risk should block countertrend entries."
        ),
    },
    "multi_timeframe": {
        "short_term_direction": choice("Classify the 1m/5m opportunity direction."),
        "higher_timeframe_direction": choice(
            "Classify the combined 15m/1h directional context."
        ),
        "timeframe_alignment": score(
            "Rate usable alignment across 1m/5m/15m/1h. Pullbacks may be valid; do not require exact agreement."
        ),
        "setup_quality": score(
            "Rate the joint multi-timeframe setup quality after costs."
        ),
        "reversal_risk": score(
            "Rate reversal risk given conflicting timeframes and structure."
        ),
    },
    "adaptive": {
        "market_regime": choice(
            "Select the current regime before selecting an action. Use only the supplied closed-candle evidence.",
            REGIMES,
        ),
        "preferred_action": choice(
            "Select the action best suited to the regime: trend following in TREND, reversion in RANGE/REVERSAL, confirmation in BREAKOUT; HOLD in UNCERTAIN/HIGH_VOLATILITY.",
            {
                "LONG": "Bullish opportunity",
                "SHORT": "Bearish opportunity",
                "HOLD": "Wait for clearer or safer evidence",
            },
        ),
        "setup_quality": score(
            "Rate the quality of the regime-appropriate setup after costs."
        ),
        "reversal_risk": score("Rate risk that the regime or direction is unstable."),
    },
}
POLICIES = {
    "trend": {
        "direction": "trend_direction",
        "quality": "trend_continuation_quality",
        "risk": "reversal_risk",
        "confirmation": "trend_strength",
    },
    "momentum": {
        "direction": "momentum_direction",
        "quality": "momentum_sustainability",
        "risk": "exhaustion_risk",
        "confirmation": "momentum_strength",
    },
    "breakout": {
        "direction": "breakout_direction",
        "quality": "breakout_quality",
        "risk": "false_breakout_risk",
        "confirmation": "breakout_confirmation",
    },
    "mean_reversion": {
        "direction": "reversion_direction",
        "quality": "reversion_quality",
        "risk": "trend_against_trade_risk",
        "confirmation": "overextension",
    },
    "multi_timeframe": {
        "direction": "short_term_direction",
        "quality": "setup_quality",
        "risk": "reversal_risk",
        "confirmation": "timeframe_alignment",
    },
    "adaptive": {
        "direction": "preferred_action",
        "quality": "setup_quality",
        "risk": "reversal_risk",
    },
}


def questions_for(definition):
    if definition.type == "baseline":
        if definition.strategy not in {"buy_hold", "ema", "rsi"}:
            raise ValueError("Unknown baseline strategy")
        return {}
    from copy import deepcopy

    questions = deepcopy(definition.questions or CATALOG.get(definition.strategy))
    if not questions:
        raise ValueError("Unknown trader style: supply questions and policy")
    for key, instruction in {
        "avoid_trade": "Should this strategy abstain because its style has ambiguous, conflicting, unsafe or insufficient evidence?",
        "long_setup": f"Is there a coherent bullish opportunity for the {definition.strategy} strategy, after the supplied trading costs?",
        "short_setup": f"Is there a coherent bearish opportunity for the {definition.strategy} strategy, after the supplied trading costs?",
    }.items():
        questions.setdefault(key, {"type": "noul", "instructions": instruction})
    policy = policy_for(definition)
    for field in ("direction", "quality", "risk"):
        if policy.get(field) not in questions:
            raise ValueError("Policy must reference existing questions")
    if questions[policy["direction"]]["type"] != "choice" or any(
        questions[policy[k]]["type"] != "score" for k in ["quality", "risk"]
    ):
        raise ValueError("Policy direction requires choice; quality/risk require score")
    if policy.get("confirmation") and (
        policy["confirmation"] not in questions
        or questions[policy["confirmation"]]["type"] != "score"
    ):
        raise ValueError("Policy confirmation requires an existing score question")
    return questions


def policy_for(definition):
    result = definition.policy or POLICIES.get(definition.strategy)
    if not result:
        raise ValueError("Unknown policy")
    return result


def runtime_for(definition, inherited):
    provider = (
        inherited.provider if definition.provider == "inherit" else definition.provider
    )
    if provider != inherited.provider and definition.model == "inherit":
        raise ValueError("An override provider requires an explicit supported model")
    model = inherited.model if definition.model == "inherit" else definition.model
    base = definition.base_url or (
        inherited.base_url
        if provider == inherited.provider
        else "https://api.typesafe.ai"
        if provider == "typesafe"
        else "http://localhost:11434"
    )
    # Reuse validation without changing process environment or loading another model.
    from urllib.parse import urlsplit

    parts = urlsplit(base)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Invalid trader endpoint")
    if not model or len(model) > 64 or any(c.isspace() for c in model):
        raise ValueError("Invalid trader model")
    base = base.rstrip("/").removesuffix("/v1/systemone").removesuffix("/v1")
    return replace(
        inherited,
        provider=provider,
        model=model,
        base_url=base,
        timeout_seconds=inherited.timeout_seconds
        if provider == inherited.provider
        else 7
        if provider == "local"
        else None,
    )


def effective_config(cfg, definition):
    result = cfg.model_copy(deep=True)
    for key, value in definition.risk_profile.model_dump(exclude_none=True).items():
        setattr(result.trading, key, value)
    result.risk.atr_stop_multiplier = definition.parameters.atr_stop_multiplier
    result.risk.reward_risk_ratio = definition.parameters.reward_risk_ratio
    result.strategy.max_holding_minutes = definition.parameters.max_holding_minutes
    result.strategy.version = "tournament-v1:" + definition.id
    return result


def definition_fingerprint(definition, runtime):
    return state_hash(
        {
            "definition": definition.model_dump(),
            "questions": questions_for(definition),
            "policy": policy_for(definition) if definition.type == "jev" else None,
            "policy_version": "tournament-policy-v1",
            "runtime": runtime.identity() if definition.type == "jev" else None,
        }
    )


def trader_action(definition, state, decision, cfg):
    if definition.type == "baseline":
        from .trading import strategy_action

        return strategy_action(definition.strategy, state, None, cfg)
    if not decision or decision.status != "OK":
        return "HOLD", decision.status if decision else "JEV_UNAVAILABLE"
    answers = JevResponse.model_validate(
        decision.raw_response, context={"questions": questions_for(definition)}
    ).answers
    p, policy = definition.parameters, policy_for(definition)
    if answers["avoid_trade"].noul >= p.avoid_probability:
        return "HOLD", "AVOID_THRESHOLD"
    if answers[policy["risk"]].score > p.max_risk:
        return "HOLD", "STRATEGY_RISK"
    if (
        answers[policy["quality"]].score < p.min_quality
        or policy.get("confirmation")
        and answers[policy["confirmation"]].score < p.min_quality
    ):
        return "HOLD", "SETUP_QUALITY"
    direction = answers[policy["direction"]].choice
    if definition.strategy == "adaptive":
        if answers["market_regime"].choice in {"UNCERTAIN", "HIGH_VOLATILITY"}:
            return "HOLD", "REGIME_ABSTAIN"
        action = direction
    else:
        action = (
            "LONG"
            if direction in {"up", "strong_up"}
            else "SHORT"
            if direction in {"down", "strong_down"}
            else "HOLD"
        )
    if action == "HOLD":
        return "HOLD", "NO_DIRECTION"
    own = answers["long_setup" if action == "LONG" else "short_setup"].noul
    opposing = answers["short_setup" if action == "LONG" else "long_setup"].noul
    if own < p.entry_probability or opposing >= p.entry_probability:
        return (
            "HOLD",
            "BELOW_THRESHOLD" if own < p.entry_probability else "CONFLICTING_SIGNALS",
        )
    return action, "JEV_" + definition.strategy.upper()

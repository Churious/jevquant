import httpx
import pytest

from app.config import load_config
from app.jev import JevClient, JevResponse, QUESTIONS


def response(long=0.8):
    answers = {}
    for key, q in QUESTIONS.items():
        if q["type"] == "noul":
            answers[key] = {
                "type": "noul",
                "noul": long if key == "long_setup" else 0.1,
            }
        elif q["type"] == "choice":
            answers[key] = {
                "type": "choice",
                "choice": "up",
                "probabilities": {k: float(k == "up") for k in q["criteria"]},
                "confidence": 1,
            }
        else:
            level = 3 if key in ("setup_quality", "momentum_quality") else 1
            answers[key] = {
                "type": "score",
                "score": level,
                "confidence": 1,
                "probabilities": {str(i): float(i == level) for i in range(5)},
                "legend": {str(i): label for i, label in enumerate(q["criteria"])},
            }
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100}}


def test_parsing_preserves_original_and_rejects_invalid():
    raw = response()
    assert JevResponse.model_validate(raw).answers["long_setup"].noul == 0.8
    raw["answers"]["long_setup"]["noul"] = float("nan")
    with pytest.raises(ValueError):
        JevResponse.model_validate(raw)
    raw = response()
    raw["answers"]["setup_quality"]["probabilities"]["3"] = 0.3
    with pytest.raises(ValueError):
        JevResponse.model_validate(raw)


async def test_missing_key_never_invents_judgment(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: pytest.fail("network call"))
    ) as client:
        result = await JevClient(client, load_config()).evaluate({})
    assert result["status"] == "JEV_UNAVAILABLE"
    assert result["request_count"] == 0


async def test_retry_and_exact_response(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "mock-test-key")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            529 if len(calls) == 1 else 200,
            json={"error": "overloaded"} if len(calls) == 1 else response(),
        )

    cfg = load_config()
    cfg.jev.backoff_seconds = 0.001
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevClient(client, cfg).evaluate({"price": {"current": 100}})
    assert result["status"] == "OK"
    assert result["request_count"] == 2
    assert result["raw_response"] == response()
    assert result["estimated_cost"] is None  # failed attempt has unknown billing

# Make the shared in-memory ledger fixture available to integration tests.
from test_trading import db  # noqa: F401

import pytest


@pytest.fixture(autouse=True)
def isolated_decision_environment(monkeypatch):
    """Tests choose their provider explicitly, independent of the running deployment."""
    for name in (
        "JEV_PROVIDER",
        "JEV_BASE_URL",
        "JEV_MODEL",
        "JEV_CONCURRENCY",
        "LOCAL_JEV_API_KEY",
        "LOCAL_JEV_TIMEOUT_SECONDS",
        "TYPESAFE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

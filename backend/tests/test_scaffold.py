from app.config import load_config


def test_safe_defaults():
    cfg = load_config()
    assert cfg.trading.starting_capital == 1000000
    assert cfg.strategy.timeframe == cfg.market.execution_timeframe == "1m"
    assert cfg.strategy.max_holding_minutes == 10
    assert cfg.market.crypto_symbols == ["BTC/KRW", "ETH/KRW"]

"""Read-only IEX authentication/freshness check; no account or order requests."""

import asyncio
import json
from datetime import datetime, timezone

import httpx

from .us_market import USStockProvider


async def check():
    async with httpx.AsyncClient(timeout=20) as client:
        provider = USStockProvider(client)
        if not provider.key or not provider.secret:
            raise SystemExit("Alpaca API 키를 서버에서 먼저 입력하세요.")
        try:
            bars = await provider.source("SPY", 3)
            latest = bars[-1] if bars else None
            print(
                json.dumps(
                    {
                        "provider": "alpaca",
                        "feed": "iex",
                        "symbol": "SPY",
                        "closed_minute_bars": len(bars),
                        "latest_timestamp": latest.timestamp.isoformat()
                        if latest
                        else None,
                        "age_seconds": (
                            datetime.now(timezone.utc) - latest.end
                        ).total_seconds()
                        if latest
                        else None,
                        "quote_currency": "USD",
                        "paper_only": True,
                        "saved_to_ledger": False,
                    }
                )
            )
        except Exception as exc:
            raise SystemExit(
                f"시세 연결 실패 ({type(exc).__name__}). 키·IEX 접근 권한을 확인하세요."
            ) from None
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(check())

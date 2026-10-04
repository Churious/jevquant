"""Read-only Kiwoom authentication/chart preflight; never logs credentials."""

import argparse
import asyncio
import json

import httpx

from .kiwoom_market import KiwoomProvider
from .korean_market import KST, aggregate_kis
from datetime import datetime


async def check(symbol):
    async with httpx.AsyncClient(timeout=20) as client:
        provider = KiwoomProvider(client)
        if not provider.key or not provider.secret:
            raise SystemExit(
                "KIWOOM_APP_KEY와 KIWOOM_SECRET_KEY를 서버 환경에 입력하세요."
            )
        try:
            rows, _ = await provider.request_minutes(symbol)
            parsed = [provider.minute_row(row) for row in rows]
            bars = aggregate_kis(parsed, symbol, "1m", datetime.now(KST))
        except Exception as exc:
            # Broker errors can contain private identifiers. Expose only our safe error type.
            raise SystemExit(
                f"키움 시세 연결 실패: {type(exc).__name__}. 키·환경·등록 IP를 확인하세요."
            ) from None
    print(
        json.dumps(
            {
                "provider": provider.name,
                "host": provider.base,
                "symbol": symbol,
                "status": "OK",
                "closed_minute_bars": len(bars),
                "latest_timestamp": bars[-1].timestamp.isoformat() if bars else None,
                "saved_to_ledger": False,
                "paper_only": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="키움 REST 시세 연결 점검")
    parser.add_argument("--symbol", default="005930")
    asyncio.run(check(parser.parse_args().symbol))

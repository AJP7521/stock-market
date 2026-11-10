"""Receive live price updates from Yahoo Finance through yfinance's WebSocket client.

No Yahoo login is needed for the stream.

Time-limited check (prints the first update for the ticker, then disconnects):
    python3 -m backend.yahoo_stream AAPL --timeout 60

Each decoded message is a dict built from Yahoo's PricingData protobuf
(yfinance/pricing.proto). Fields used here:
    id           ticker symbol
    price        last trade price in the quote currency (protobuf float32; e.g. 348.01)
    time         source time, int64 **milliseconds** since the Unix epoch (UTC);
                 MessageToDict renders int64 values as strings ("1794338162000")
    day_volume   shares traded so far in the current session: a running total,
                 not the size of this trade (string, e.g. "16325014")
    last_size    shares in the most recent trade, when Yahoo supplies it ("45")
    market_hours 1 was observed during the regular session
"""

import argparse
import asyncio
from datetime import datetime, timezone

from yfinance import AsyncWebSocket

VOLUME_FIELDS = ("day_volume", "last_size", "vol_24hr")


def utc_from_ms(ms) -> str:
    """Epoch milliseconds (int or numeric string) -> ISO 8601 UTC with milliseconds."""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def summarize(message: dict, received_at: datetime) -> dict:
    """Pick out the fields this project cares about from one decoded update."""
    source_ms = message.get("time")
    return {
        "ticker": message.get("id"),
        "price": message.get("price"),
        "source_time_raw": source_ms,
        "source_time_utc": utc_from_ms(source_ms) if source_ms is not None else None,
        "received_at_utc": received_at.isoformat(timespec="milliseconds"),
        "receipt_minus_source_ms": (
            round(received_at.timestamp() * 1000) - int(source_ms) if source_ms is not None else None
        ),
        "volume_fields": {k: message[k] for k in VOLUME_FIELDS if k in message},
        "market_hours": message.get("market_hours"),
        "raw": message,
    }


async def first_update(ticker: str, timeout: float) -> dict:
    """Subscribe to one ticker and wait at most `timeout` seconds for one update.

    Returns {"connected": bool, "update": summary or None, "error": str or None}.
    """
    ws = AsyncWebSocket(verbose=False)
    got: asyncio.Future = asyncio.get_running_loop().create_future()
    result = {"connected": False, "update": None, "error": None}

    def handler(message: dict) -> None:
        if not got.done() and message.get("id") == ticker:
            got.set_result(summarize(message, datetime.now(timezone.utc)))

    listener = None
    try:
        await ws.subscribe(ticker)  # opens the connection, then sends {"subscribe": [ticker]}
        result["connected"] = ws._ws is not None
        listener = asyncio.create_task(ws.listen(handler))
        result["update"] = await asyncio.wait_for(got, timeout)
    except asyncio.TimeoutError:
        result["error"] = f"no {ticker} update within {timeout:g} s"
    except Exception as exc:  # connection refused, DNS failure, etc.
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if listener:
            listener.cancel()
        try:
            if result["connected"]:
                await ws.unsubscribe(ticker)
            await ws.close()
        except Exception:
            pass
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Time-limited Yahoo Finance stream check")
    parser.add_argument("ticker", nargs="?", default="AAPL")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    print(f"started_utc: {started.isoformat(timespec='seconds')}  ticker: {args.ticker}  timeout: {args.timeout:g}s")
    result = asyncio.run(first_update(args.ticker, args.timeout))
    print(f"connection_opened: {result['connected']}")
    update = result["update"]
    if update is None:
        print(f"receipt: UNVERIFIED ({result['error']})")
        return
    for key in ("ticker", "price", "source_time_raw", "source_time_utc", "received_at_utc",
                "receipt_minus_source_ms", "volume_fields", "market_hours"):
        print(f"{key}: {update[key]}")
    print(f"raw_message: {update['raw']}")


if __name__ == "__main__":
    main()

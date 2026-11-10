"""Live Yahoo Finance feed running inside FastAPI.

One SymbolFeed per ticker: it subscribes with yfinance's AsyncWebSocket (the same
client as backend/yahoo_stream.py), records every message with backend.recorder,
and notifies listeners (FastAPI WebSocket handlers) with each new observation.

The receive loop is our own rather than AsyncWebSocket.listen(). In yfinance 1.8.0,
listen() reconnects by reusing the closed socket, and it spins if Yahoo closes the
connection cleanly, so it cannot report a dropped feed. We still use yfinance's
subscribe() and message decoder.

Feed status values:
  connecting     opening the upstream connection
  live           connected, and a message arrived within QUIET_AFTER_S
  quiet          connected, but no message for QUIET_AFTER_S (e.g. market closed)
  disconnected   upstream connection lost; retrying every RETRY_S
  paused         upstream disconnected on request (POST /api/live/{symbol}/disconnect)
"""

import asyncio
import contextlib
import json
import logging

from yfinance import AsyncWebSocket

from backend.recorder import connect, db_path, now_ms, record

log = logging.getLogger("live")

QUIET_AFTER_S = 30
RETRY_S = 5
RESUBSCRIBE_S = 15       # yfinance re-sends its subscription on the same interval


class SymbolFeed:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.state = "connecting"
        self.detail = ""
        self.last_source_ms: int | None = None
        self.last_received_ms: int | None = None
        self.messages = 0
        self.listeners: set[asyncio.Queue] = set()
        self.users = 0
        self._paused = asyncio.Event()
        self._ws: AsyncWebSocket | None = None
        self._task: asyncio.Task | None = None
        self._conn = connect(db_path())

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"feed-{self.symbol}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._close_upstream()
        self._conn.close()

    async def pause(self) -> None:
        """Disconnect upstream on request and stay disconnected until resume()."""
        self._paused.set()
        await self._close_upstream()
        self._set("paused", "upstream disconnected on request")

    def resume(self) -> None:
        self._paused.clear()

    # ---- status ----------------------------------------------------------
    def status(self) -> dict:
        state = self.state
        if state == "live" and self.last_received_ms and now_ms() - self.last_received_ms > QUIET_AFTER_S * 1000:
            state = "quiet"
        return {
            "status": state,
            "detail": self.detail,
            "source": "yahoo",
            "last_source_time_ms": self.last_source_ms,
            "last_received_time_ms": self.last_received_ms,
            "messages": self.messages,
            "server_time_ms": now_ms(),
        }

    def _set(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            log.info("%s feed: %s %s", self.symbol, state, detail)
        self.state, self.detail = state, detail
        self._notify({"kind": "feed"})

    def _notify(self, event: dict) -> None:
        for q in list(self.listeners):
            q.put_nowait(event)

    # ---- upstream loop ---------------------------------------------------
    async def _close_upstream(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def _run(self) -> None:
        while True:
            if self._paused.is_set():
                await asyncio.sleep(0.5)
                continue
            self._set("connecting")
            resubscriber = None
            try:
                self._ws = AsyncWebSocket(verbose=False)
                await self._ws.subscribe(self.symbol)          # opens the connection
                self._set("live", "subscribed; waiting for first message" if not self.messages else "")
                resubscriber = asyncio.create_task(self._resubscribe(self._ws))
                async for raw in self._ws._ws:                  # websockets connection opened by subscribe()
                    self._handle(raw)
                if not self._paused.is_set():
                    self._set("disconnected", "upstream closed the connection")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._paused.is_set():
                    self._set("disconnected", f"{type(exc).__name__}: {exc}")
            finally:
                if resubscriber:
                    resubscriber.cancel()
                await self._close_upstream()
            if not self._paused.is_set():
                await asyncio.sleep(RETRY_S)

    async def _resubscribe(self, ws: AsyncWebSocket) -> None:
        while True:
            await asyncio.sleep(RESUBSCRIBE_S)
            await ws.subscribe(self.symbol)

    def _handle(self, raw: str) -> None:
        received = now_ms()
        try:
            message = self._ws._decode_message(json.loads(raw).get("message", "")) if self._ws else {}
            if "error" in message or message.get("id") != self.symbol:
                return
            result = record(self._conn, message, received)
        except (ValueError, KeyError) as exc:   # undecodable or incomplete message: skip it, keep the feed
            log.warning("%s: skipped message: %s", self.symbol, exc)
            return
        self.messages += 1
        self.last_source_ms = int(message["time"])
        self.last_received_ms = received
        if self.state != "live" or self.detail:
            self._set("live")
        self._notify({"kind": "observation", "source_time_ms": self.last_source_ms,
                      "observation_id": result["observation_id"], "is_repeat": result["is_repeat"]})


class LiveHub:
    """Starts one feed per symbol while at least one browser is watching it."""

    def __init__(self):
        self.feeds: dict[str, SymbolFeed] = {}

    def acquire(self, symbol: str) -> SymbolFeed:
        feed = self.feeds.get(symbol)
        if feed is None:
            feed = self.feeds[symbol] = SymbolFeed(symbol)
            feed.start()
        feed.users += 1
        return feed

    async def release(self, feed: SymbolFeed) -> None:
        feed.users -= 1
        if feed.users <= 0 and self.feeds.get(feed.symbol) is feed:
            del self.feeds[feed.symbol]
            await feed.stop()

    async def stop_all(self) -> None:
        for feed in list(self.feeds.values()):
            await feed.stop()
        self.feeds.clear()

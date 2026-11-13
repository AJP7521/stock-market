"""Live feed status when the upstream is unavailable, with a FAKE upstream socket (no network, no Yahoo).

The scripted upstream drops the connection by itself (network error), refuses the next connection (Yahoo
unreachable), then works again; another script closes the connection cleanly. The feed must report `disconnected`
with the reason, retry, return to `live`, and keep recording observations to SQLite either side of the outage. With
no message for QUIET_AFTER_S it reports `quiet`.

    python3 -m unittest backend.tests.test_live_feed -v
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import backend.recorder as recorder
from backend import live
from backend.recorder import connect
from backend.tests.test_candles import ms

SYMBOL = "TESTFEED"


def message(hms: str, price: float, cum: int) -> str:
    """A raw upstream frame; the fake decoder returns the inner dict as yfinance's decoder would."""
    return json.dumps({"message": {"id": SYMBOL, "price": price, "time": str(ms(hms)), "day_volume": str(cum)}})


class FakeConnection:
    def __init__(self, frames, ending):
        self.frames, self.ending = list(frames), ending

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0.01)
        if self.frames:
            return self.frames.pop(0)
        if self.ending == "drop":
            raise ConnectionError("network lost")
        if self.ending == "close":
            raise StopAsyncIteration
        await asyncio.Event().wait()                     # "stay": connected, no more messages


class FakeAsyncWebSocket:
    """Stands in for yfinance.AsyncWebSocket; each new instance takes the next scripted connection."""
    script: list = []

    def __init__(self, verbose=False):
        self.plan = FakeAsyncWebSocket.script.pop(0) if FakeAsyncWebSocket.script else ("stay", [])
        self._ws = None

    async def subscribe(self, symbol):
        if self.plan[0] == "refuse":
            raise OSError("Yahoo unreachable")
        if self._ws is None:
            self._ws = FakeConnection(self.plan[1], self.plan[0])

    async def close(self):
        pass

    def _decode_message(self, inner):
        return inner


class LiveFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test-feed.sqlite"
        self.patches = [mock.patch.dict(os.environ, {"MARKET_DB": str(self.db)}),
                        mock.patch.object(live, "AsyncWebSocket", FakeAsyncWebSocket),
                        mock.patch.object(live, "RETRY_S", 0.05)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        recorder.TEST_NOW_MS = None
        self.tmp.cleanup()

    def run_feed(self, script, until):
        """Run a feed until `until(states)` is true (or 3 s pass); return the (state, detail) transitions."""
        FakeAsyncWebSocket.script = list(script)
        states = []
        original = live.SymbolFeed._set

        def spy(feed, state, detail=""):
            if (state, detail) != (feed.state, feed.detail) or not states:
                states.append((state, detail))
            original(feed, state, detail)

        async def main():
            with mock.patch.object(live.SymbolFeed, "_set", spy):
                feed = live.SymbolFeed(SYMBOL)
                feed.start()
                for _ in range(300):
                    if until(states):
                        break
                    await asyncio.sleep(0.01)
                status = feed.status()
                recorder.TEST_NOW_MS = feed.last_received_ms + (live.QUIET_AFTER_S + 1) * 1000
                quiet = feed.status()["status"]
                recorder.TEST_NOW_MS = None
                messages = feed.messages
                await feed.stop()
                return status, quiet, messages

        status, quiet, messages = asyncio.run(main())
        return states, status, quiet, messages

    def stored(self):
        conn = connect(self.db)
        try:
            return [tuple(r) for r in conn.execute("SELECT price, source FROM observations ORDER BY id")]
        finally:
            conn.close()

    def test_unrequested_drop_then_unreachable_then_recovers(self):
        script = [("drop", [message("15:00:05", 100, 1000)]),        # live, then the network drops
                  ("refuse", []),                                     # reconnect fails: Yahoo unreachable
                  ("stay", [message("15:00:30", 101, 1005)])]         # reconnect works
        states, status, quiet, messages = self.run_feed(
            script, lambda s: sum(1 for st, _ in s if st == "live") >= 2 and len(self.stored_safe()) >= 2)
        names = [st for i, (st, _) in enumerate(states) if i == 0 or st != states[i - 1][0]]   # state changes
        self.assertEqual(names, ["connecting", "live", "disconnected", "connecting", "disconnected", "connecting",
                                 "live"])
        self.assertEqual(states[1], ("live", "subscribed; waiting for first message"))
        self.assertIn(("disconnected", "ConnectionError: network lost"), states)
        self.assertIn(("disconnected", "OSError: Yahoo unreachable"), states)
        self.assertEqual((status["status"], messages), ("live", 2))
        self.assertEqual(status["last_source_time_ms"], ms("15:00:30"))
        self.assertEqual(quiet, "quiet")                                   # no message for QUIET_AFTER_S + 1 s
        self.assertEqual(self.stored(), [(100.0, "yahoo"), (101.0, "yahoo")])   # recorded either side of the outage

    def test_upstream_closes_cleanly_then_reconnects(self):
        script = [("close", [message("15:00:05", 100, 1000)]), ("stay", [message("15:00:30", 101, 1005)])]
        states, status, _quiet, messages = self.run_feed(
            script, lambda s: sum(1 for st, _ in s if st == "live") >= 2 and len(self.stored_safe()) >= 2)
        self.assertIn(("disconnected", "upstream closed the connection"), states)
        self.assertEqual((status["status"], messages), ("live", 2))

    def stored_safe(self):
        try:
            return self.stored()
        except Exception:
            return []


if __name__ == "__main__":
    unittest.main()

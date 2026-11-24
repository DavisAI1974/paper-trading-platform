"""The live DXLink socket reader of the paper runner (builder run B2). Reads only; sends only the DXLink handshake,
the subscription and the keepalive. No reconnect: a drop raises StreamDropped and the run ends.

RULE G4: copied from tasty/tasty_7e_check.py (not imported there); the source lines are named beside each part.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse

from tasty.paper.broker_tasty import (StepStop, USER_AGENT, add_secret, dget, http, learn_secrets, redact, scrub)

# copied from tasty/tasty_7e_check.py lines 48, 52-60
EVENT_TYPES = ["Quote", "Trade", "TradeETH", "TimeAndSale", "Summary", "Profile", "Order"]
KEEPALIVE_SECONDS = 30      # the documented interval
AGGREGATION_PERIOD = 0      # 0 = the server does not group events over a period: we ask for every event
SETUP_VERSION = "0.1-DXF-JS/0.3.0"
KEEPALIVE_TIMEOUT = 60      # keepaliveTimeout and acceptKeepaliveTimeout in SETUP
FEED_CHANNEL = 3
DATA_FORMAT = "FULL"
STOP_POLL_SECONDS = 1.0     # new here: how often the receive loop looks at the stop flags


class StreamDropped(Exception):
    """The connection was closed by the server. No reconnect in this build."""


def quote_token(ctx):
    """Copied from tasty/tasty_7e_check.py lines 641-650 (the quote token and the wss-only check)."""
    _, q = http(ctx, "GET", "/api-quote-tokens")
    d = dget(q, "data") if isinstance(dget(q, "data"), dict) else {}
    url = d.get("dxlink-url")
    if not d.get("token") or not url:
        raise StepStop("quote token response had no token or dxlink-url")
    add_secret(d["token"])
    parts = urllib.parse.urlparse(url)
    ctx["rec"].note(f"dxlink-url scheme {parts.scheme}, host {parts.netloc}")
    if parts.scheme != "wss":
        raise StepStop(f"dxlink-url scheme is {parts.scheme!r}, not wss: refusing to send the quote token")
    return url, d["token"]


# copied from tasty/tasty_7e_check.py lines 553-627 (stream). Changes, each small: every received frame goes to
# on_frame(raw, t_ns, msg_no) instead of the stats; the stop is should_stop() (and the receive waits at most
# STOP_POLL_SECONDS so it is looked at); a closed connection raises StreamDropped; `rec` is the feed recorder.
async def stream(url, token, symbols, rec, on_frame, should_stop):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed
    host = url.split("/")[2]
    st = {"next_ka": time.monotonic() + KEEPALIVE_SECONDS, "auth_sent": False, "n": 0}

    # ping_interval=None: no extra traffic of ours; DXLink has its own KEEPALIVE
    async with connect(url, user_agent_header=USER_AGENT, ping_interval=None, max_size=None) as ws:
        async def send(msg):
            t = time.time_ns()
            await ws.send(json.dumps(msg))
            rec.write(t_ns=t, kind="sent", host=host, body=redact(msg))

        async def recv_record():
            """Next message (recorded), or None once the stop condition is reached."""
            while True:
                now = time.monotonic()
                if should_stop():
                    return None
                if now >= st["next_ka"]:
                    await send({"type": "KEEPALIVE", "channel": 0})
                    st["next_ka"] = now + KEEPALIVE_SECONDS
                wait = min(st["next_ka"] - now, STOP_POLL_SECONDS)
                try:
                    raw = await asyncio.wait_for(ws.recv(), max(wait, 0.01))
                except asyncio.TimeoutError:
                    continue
                except ConnectionClosed as e:
                    raise StreamDropped(f"connection closed by server: {e}") from e
                t = time.time_ns()   # taken when the websocket library hands the frame over
                st["n"] += 1
                try:
                    msg = json.loads(raw)
                except ValueError:
                    msg = {}
                learn_secrets(msg)   # a secret first seen in a frame is learned before the frame is written
                on_frame(scrub(raw), t, st["n"], host)
                if not isinstance(msg, dict):
                    msg = {}
                if st["auth_sent"] and msg.get("type") == "AUTH_STATE" and msg.get("state") == "UNAUTHORIZED":
                    raise StepStop("AUTH_STATE UNAUTHORIZED after our AUTH was sent")
                return msg

        async def wait_for(test, what):
            while True:
                msg = await recv_record()
                if msg is None:
                    raise StepStop(f"stop condition reached before {what}")
                if msg.get("type") == "ERROR":
                    raise StepStop(f"server sent ERROR before {what} (see the record)")
                if test(msg):
                    return msg

        await send({"type": "SETUP", "channel": 0, "version": SETUP_VERSION,
                    "keepaliveTimeout": KEEPALIVE_TIMEOUT, "acceptKeepaliveTimeout": KEEPALIVE_TIMEOUT})
        await wait_for(lambda m: m.get("type") == "AUTH_STATE", "AUTH_STATE")
        await send({"type": "AUTH", "channel": 0, "token": token})
        st["auth_sent"] = True
        await wait_for(lambda m: m.get("type") == "AUTH_STATE" and m.get("state") == "AUTHORIZED", "AUTHORIZED")
        await send({"type": "CHANNEL_REQUEST", "channel": FEED_CHANNEL, "service": "FEED", "parameters": {"contract": "AUTO"}})
        await wait_for(lambda m: m.get("type") == "CHANNEL_OPENED" and m.get("channel") == FEED_CHANNEL, "CHANNEL_OPENED")
        await send({"type": "FEED_SETUP", "channel": FEED_CHANNEL, "acceptAggregationPeriod": AGGREGATION_PERIOD,
                    "acceptDataFormat": DATA_FORMAT})
        await wait_for(lambda m: m.get("type") == "FEED_CONFIG", "FEED_CONFIG")
        await send({"type": "FEED_SUBSCRIPTION", "channel": FEED_CHANNEL, "reset": True,
                    "add": [{"type": t, "symbol": s} for s in symbols for t in EVENT_TYPES]})
        while await recv_record() is not None:
            pass
    # leaving the block closes the socket; nothing more is sent

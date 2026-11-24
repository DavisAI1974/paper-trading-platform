"""The order path of the paper runner: HTTP to the tastytrade SANDBOX only (builder run B2).

RULE G4: tasty/tasty_7e_check.py is not edited and not imported. The parts below are COPIES of the proven 7e code,
each marked with its source lines. A copy is kept as it was; where a copy had to change to fit here, the change is
named beside it.

An order: a fresh sign-in token, a dry-run, then ONE submit that is never re-sent. An uncertain submit goes to the
look-up by external-identifier. An order's outcome is read from its FILLS (and the look-up), never from the status
alone. A fill price is written as the sandbox said it, beside the bid and the offer of the print acted at, each on
its own; nothing is computed from it. No fee, slippage, mid price or fill assumption anywhere.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

# copied from tasty/tasty_7e_check.py lines 35-61 (the sandbox rows only: production is not reachable from here)
USER_AGENT = "davisai-paper/0.1"
SANDBOX_HOST = "api.cert.tastyworks.com"
BASES = {"sandbox": "https://" + SANDBOX_HOST}
CRED_NAMES = {"sandbox": ("TASTY_SANDBOX_CLIENT_ID", "TASTY_SANDBOX_CLIENT_SECRET", "TASTY_SANDBOX_REFRESH_TOKEN")}
SECRET_WORDS = ("token", "secret", "authorization", "password", "cookie")   # key contains one of these
SECRET_KEYS = {"client_id", "id_token"}                                   # or equals one of these
TERMINAL = {"filled", "cancelled", "expired", "rejected", "removed", "partially removed"}
HTTP_TIMEOUT_SECONDS = 30   # transport limit for one HTTP call (our choice, see receipt)
MAX_ATTEMPTS = 4            # for 429 and 5xx, never for an order submit
BACKOFF_RULE = "sleep 2**attempt seconds plus uniform(0,1) seconds, between attempts"
LEARN_FLOOR = 8             # shortest string learned as a secret from a response (credentials: any length)

# new here (our choices, all on the receipt): how an order's fills are waited for after the submit
POLL_COUNT = 10
POLL_SECONDS = 1
LOOKUP_MAX_PAGES = 5
ORDER_TIME_IN_FORCE = "Day"
ORDER_TYPE = "Market"
ORDER_QUANTITY = 1
HALT_ET_HOURS = (17, 18)    # no order of any kind from 5 pm to 6 pm Eastern by the wall clock
ET = ZoneInfo("America/New_York")
FLATTEN_NOTE = "flattened at the stop: not trade data"

SECRETS = set()   # every secret string this run knows; scrubbed out of everything written or printed


# copied from tasty/tasty_7e_check.py lines 66-72
class StepStop(Exception):
    """This step cannot go on; what was seen is already recorded."""


class Uncertain(Exception):
    """A request may or may not have reached the server."""


class HostRefused(Exception):
    """An order was asked for on a host that is not the sandbox."""


# copied from tasty/tasty_7e_check.py lines 74-80
class NoRedirect(urllib.request.HTTPRedirectHandler):
    # never follow a redirect: a 3xx is recorded and stops the step
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def check_order_host(base):
    """The sandbox guard (the 7e check had a production guard of the same place and purpose, lines 231-239; this
    one is the reverse: the ONLY host an order may go to is the sandbox). Raises HostRefused for any other."""
    host = str(base).split("//", 1)[-1].split("/", 1)[0]
    if not str(base).startswith("https://") or host != SANDBOX_HOST:
        raise HostRefused(f"order host {host!r} refused: only {SANDBOX_HOST} (the sandbox) is allowed")


# copied from tasty/tasty_7e_check.py lines 85-140 (is_secret_key, add_secret, learn_secrets, scrub, redact, say, dget)
def is_secret_key(k):
    n = str(k).lower().replace("-", "_")
    return n in SECRET_KEYS or any(w in n for w in SECRET_WORDS)


def add_secret(s):
    if isinstance(s, str) and len(s) >= LEARN_FLOOR:
        SECRETS.add(s)


def learn_secrets(x):
    """Every string value under a secret-looking key becomes a known secret."""
    if isinstance(x, dict):
        for k, v in x.items():
            if is_secret_key(k) and isinstance(v, str):
                add_secret(v)      # learned from a response: the 8-character floor applies
            learn_secrets(v)
    elif isinstance(x, list):
        for v in x:
            learn_secrets(v)


def scrub(text):
    """Replace every known secret string (and its JSON-escaped form) by <redacted>."""
    text = str(text)
    # the server may write "/" as "\/": match that form too, and the JSON-escaped form of each
    base = set(SECRETS) | {x.replace("/", "\\/") for x in SECRETS}
    # one level of JSON escaping (as the server writes it), with and without non-ASCII escaped, and the
    # second level (as raw server text looks once our own line is written around it)
    once = {json.dumps(x)[1:-1] for x in base} | {json.dumps(x, ensure_ascii=False)[1:-1] for x in base}
    forms = base | once | {json.dumps(x)[1:-1] for x in once}
    for s in sorted(forms, key=len, reverse=True):
        text = text.replace(s, "<redacted>")
    return text


def redact(x):
    """Copy of x with the value of every secret-looking key replaced by <redacted>."""
    if isinstance(x, dict):
        return {k: ("<redacted>" if is_secret_key(k) else redact(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [redact(v) for v in x]
    return x


def say(text):
    print(scrub(text))


def dget(x, *keys):
    """x[k1][k2]... where any level that is not an object gives None."""
    for k in keys:
        if not isinstance(x, dict):
            return None
        x = x.get(k)
    return x


# copied from tasty/tasty_7e_check.py lines 145-164 (stamp, sha256_file, code_commit)
def stamp(ns):
    """UTC time to the nanosecond, safe in a file name."""
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime(ns // 10**9)) + f"{ns % 10**9:09d}Z"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_commit():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# copied from tasty/tasty_7e_check.py lines 167-174 (credentials from the environment by NAME; "env" is always sandbox)
def read_creds(env="sandbox"):
    """Credentials from the environment only. Names a missing variable, never a value."""
    missing = [n for n in CRED_NAMES[env] if not os.environ.get(n)]
    if missing:
        sys.exit("missing environment variable(s): " + ", ".join(missing))
    cid, secret, refresh = (os.environ[n] for n in CRED_NAMES[env])
    SECRETS.update((cid, secret, refresh))   # known secrets, whatever their length
    return {"client_id": cid, "client_secret": secret, "refresh_token": refresh}


# copied from tasty/tasty_7e_check.py lines 177-191 (the banned list also names this code repository by its own
# location, as the 7e check does; "/home/user/Markets" is kept as it was)
def check_out_dir(out):
    """Refuse a folder inside the Markets repo (or inside the repo this file lives in)."""
    real = os.path.realpath(out)
    here = os.path.dirname(os.path.abspath(__file__))
    banned = [os.path.realpath("/home/user/Markets")]
    try:
        top = subprocess.run(["git", "-C", here, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True).stdout.strip()
        banned.append(os.path.realpath(top))
    except (OSError, subprocess.CalledProcessError):
        pass
    for b in banned:
        if os.path.commonpath([real, b]) == b:
            sys.exit(f"--out must not be inside the Markets repo ({b})")
    return real


# copied from tasty/tasty_7e_check.py lines 196-226 (new_file, write_text_file, Recorder). Change: LockedRecorder
# only adds a lock, because the order worker and the feed loop both write.
def new_file(ctx, name):
    """Create a file (exclusive: never rewrites one) and track it at creation."""
    path = os.path.join(ctx["out"], name)
    f = open(path, "xb")
    ctx["files"].append(path)
    return f


def write_text_file(ctx, name, text):
    f = new_file(ctx, name)
    with f:
        f.write(scrub(text).encode())


class Recorder:
    """Append-only JSONL file; one line per request, response, note or stream message.
    Every line is scrubbed of known secrets here, so nothing can bypass it."""

    def __init__(self, ctx, name):
        self.f = new_file(ctx, name)

    def write(self, **fields):
        line = scrub(json.dumps(fields, separators=(",", ":"), default=str))
        self.f.write(line.encode() + b"\n")
        self.f.flush()

    def note(self, text):
        self.write(t_ns=time.time_ns(), kind="note", text=text)

    def close(self):
        self.f.close()


class LockedRecorder(Recorder):
    def __init__(self, ctx, name):
        super().__init__(ctx, name)
        self.lock = threading.Lock()

    def write(self, **fields):
        with self.lock:
            super().write(**fields)


def make_ctx(out, base=None):
    """The context http() works in. `base` is checked against the sandbox host here, before anything is made."""
    base = BASES["sandbox"] if base is None else base
    check_order_host(base)
    return {"env": "sandbox", "base": base, "creds": None, "token": None, "out": out, "files": [], "rec": None,
            "failed": []}


# copied from tasty/tasty_7e_check.py lines 242-301 (http). Changes: the base comes from ctx["base"], checked by
# check_order_host on every call (7e: BASES[env] and the production guard); the prod-only lines are dropped.
def http(ctx, method, path, body=None, retry=True, auth=True):
    """One REST call with recording, backoff on 429/5xx (retry=True only), and one re-mint on 401
    (retry=True only). With retry=False (an order submit) nothing is ever sent twice: a network
    failure, a 401 or a 5xx raises Uncertain. Raises StepStop on any other non-2xx."""
    base = ctx["base"]
    check_order_host(base)
    host = base.split("//", 1)[1]
    attempts = MAX_ATTEMPTS if retry else 1
    attempt, reminted = 0, False
    while True:
        attempt += 1
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if auth and ctx.get("token"):
            headers["Authorization"] = "Bearer " + ctx["token"]
        ctx["rec"].write(t_ns=time.time_ns(), kind="request", host=host, method=method, path=path,
                         attempt=attempt, headers=redact(headers), body=redact(body))
        status, text, hdrs, failure = None, "", {}, None
        try:
            req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
            with OPENER.open(req, timeout=HTTP_TIMEOUT_SECONDS) as r:
                status, text, hdrs = r.status, r.read().decode("utf-8", "replace"), dict(r.headers.items())
        except urllib.error.HTTPError as e:
            status, text, hdrs = e.code, e.read().decode("utf-8", "replace"), dict(e.headers.items())
        except OSError as e:   # timeouts, resets, DNS
            failure = f"{type(e).__name__}: {e}"
        t_ns = time.time_ns()
        try:
            obj = json.loads(text) if text else None
        except ValueError:
            obj = None
        learn_secrets(obj)   # before anything is written: the new access token must be scrubbed too
        if failure:
            ctx["rec"].write(t_ns=t_ns, kind="response", host=host, method=method, path=path,
                             attempt=attempt, status=None, error=failure)
        else:
            rec = dict(t_ns=t_ns, kind="response", host=host, method=method, path=path,
                       attempt=attempt, status=status, headers=redact(hdrs), body_text=scrub(text))
            if obj is not None:
                rec["body"] = redact(obj)
            ctx["rec"].write(**rec)
        if status == 401 and retry and auth and not reminted and path != "/oauth/token":
            reminted = True       # token expired: mint once and send the same request again
            attempt -= 1
            mint(ctx)
            continue
        if status is not None and 200 <= status < 300:
            return status, obj
        again = failure is not None or status == 429 or status >= 500
        if retry and again and attempt < attempts:
            time.sleep(2 ** attempt + random.uniform(0, 1))   # backoff with jitter
            continue
        if not retry and (failure or status == 401 or status >= 500):
            raise Uncertain(failure or f"HTTP {status}")
        raise StepStop(f"{method} {path}: " + (failure or f"HTTP {status}"))


# copied from tasty/tasty_7e_check.py lines 304-314 (mint; the prod scope line is dropped)
def mint(ctx):
    """Mint a fresh 15-minute access token from the refresh token."""
    ctx["token"] = None
    body = {"grant_type": "refresh_token", **ctx["creds"]}
    _, obj = http(ctx, "POST", "/oauth/token", body, auth=False)
    if not obj or "access_token" not in obj:
        raise StepStop("token response had no access_token")
    add_secret(obj["access_token"])
    ctx["token"] = obj["access_token"]


# copied from tasty/tasty_7e_check.py lines 317-326 (try_call)
def try_call(ctx, method, path, **kw):
    """A step that may fail without ending the run: the failure is recorded and printed."""
    try:
        status, obj = http(ctx, method, path, **kw)
        say(f"  {method} {path} -> {status}")
        return obj
    except (StepStop, Uncertain) as e:
        ctx["failed"].append({"step": f"{method} {path}", "note": scrub(str(e))})
        say(f"  step stopped: {e}")
        return None


# copied from tasty/tasty_7e_check.py lines 340-346 (has_identifier)
def has_identifier(x, ext):
    """True if any order object in x has external-identifier equal to ext."""
    if isinstance(x, dict):
        return x.get("external-identifier") == ext or any(has_identifier(v, ext) for v in x.values())
    if isinstance(x, list):
        return any(has_identifier(v, ext) for v in x)
    return False


def find_order(x, ext):
    """New (the 7e look-up only said found / not found): the order object itself whose external-identifier is ext."""
    if isinstance(x, dict):
        if x.get("external-identifier") == ext:
            return x
        for v in x.values():
            f = find_order(v, ext)
            if f is not None:
                return f
    elif isinstance(x, list):
        for v in x:
            f = find_order(v, ext)
            if f is not None:
                return f
    return None


# copied from tasty/tasty_7e_check.py lines 349-378 (check_uncertain_submit). Change: it also returns the order
# object when found (found_order), so the outcome can be read from its fills; max_pages is LOOKUP_MAX_PAGES.
def check_uncertain_submit(ctx, acct, ext, max_pages):
    """After an uncertain submit: look in the live orders and in the orders since the previous day
    (US Eastern), page by page (each page recorded) until the identifier is found, a page is empty,
    the response's own pagination says no more pages, or max_pages is reached.
    Outcomes: found / not found / not found within N pages / could not check."""
    prev = (datetime.datetime.now(ZoneInfo("America/New_York")) - datetime.timedelta(days=1)).date().isoformat()
    result, found_order = {}, None
    for label, base, sep in (("live", f"/accounts/{acct}/orders/live", "?"),
                             ("since_previous_day_et", f"/accounts/{acct}/orders?start-date={prev}", "&")):
        outcome, more = "not found", False
        for page in range(max_pages):
            obj = try_call(ctx, "GET", base + (f"{sep}page-offset={page}" if page else ""))
            if obj is None:
                outcome = "could not check"
                break
            if has_identifier(obj, ext):
                outcome = "found"
                found_order = find_order(obj, ext)
                break
            pag = dget(obj, "pagination")
            total = dget(pag, "total-pages")
            more = bool(dget(obj, "data", "items")) and isinstance(total, int) and page + 1 < total
            if not more:    # empty page, or the response says there are no more (or says nothing)
                break
        else:
            more = True
        if outcome == "not found" and more:
            outcome = f"not found within {max_pages} pages"
        result[label] = outcome
        if found_order is not None:
            break
    ctx["rec"].note(f"uncertain submit, external-identifier {ext}: {json.dumps(result)}")
    say(f"  uncertain submit; external-identifier check: {json.dumps(result)}")
    return result, found_order


# ---------------------------------------------------------------- the readers (new): fills and positions

def order_object(x):
    """The order dict from {'data': {...order...}} or from the order itself."""
    d = dget(x, "data")
    return d if isinstance(d, dict) and ("legs" in d or "status" in d or "id" in d) else (x if isinstance(x, dict) else {})


def read_fills(x):
    """Every fill of an order, from its legs' own `fills` lists, as the sandbox said them (strings kept)."""
    out = []
    for leg in (order_object(x).get("legs") or []):
        for f in (leg.get("fills") or []):
            out.append({"fill_id": f.get("fill-id"), "fill_price": f.get("fill-price"), "quantity": f.get("quantity"),
                        "filled_at": f.get("filled-at"), "venue": f.get("destination-venue"),
                        "leg_action": leg.get("action"), "symbol": leg.get("symbol")})
    return out


def read_outcome(x, ordered_quantity=ORDER_QUANTITY):
    """What an order came to, from its FILLS. The status is carried as said and decides nothing: an order whose
    status stayed 'Live' with a recorded fill is a filled order."""
    o = order_object(x)
    fills = read_fills(x)
    filled = sum(f["quantity"] for f in fills if isinstance(f["quantity"], (int, float)))
    return {"order_id": o.get("id"), "status_as_said": o.get("status"), "external_identifier": o.get("external-identifier"),
            "fills": fills, "filled_quantity": filled, "confirmed": filled >= ordered_quantity}


def read_positions(x):
    """Open positions from a /positions response: symbol, quantity as said, direction as said, signed quantity
    (Long +, Short -), average open price as said."""
    out = []
    for it in (dget(x, "data", "items") or []):
        q = it.get("quantity")
        d = it.get("quantity-direction")
        signed = (-q if d == "Short" else q) if isinstance(q, (int, float)) and d in ("Long", "Short") else None
        out.append({"symbol": it.get("symbol"), "quantity": q, "direction": d, "signed_quantity": signed,
                    "average_open_price": it.get("average-open-price"), "instrument_type": it.get("instrument-type")})
    return out


def net_position(positions, symbol):
    """Sum of the signed quantity of the positions in `symbol` (a count). None if a position could not be signed."""
    sel = [p for p in positions if p["symbol"] == symbol]
    if any(p["signed_quantity"] is None for p in sel):
        return None
    return sum(p["signed_quantity"] for p in sel)


# ---------------------------------------------------------------- wall clock

def in_halt_hour(ns=None):
    """True from 5 pm to 6 pm US Eastern on the WALL clock (or on the clock value `ns` given)."""
    ns = time.time_ns() if ns is None else ns
    h = datetime.datetime.fromtimestamp(ns / 1e9, ET).hour
    return HALT_ET_HOURS[0] <= h < HALT_ET_HOURS[1]


# ---------------------------------------------------------------- the order

def write_intent(rec, intent, sent, reason):
    rec.write(t_ns=time.time_ns(), kind="intent", sent=sent, reason=reason, **intent)


class Broker:
    """One sandbox account. send() is run in ONE worker, one order at a time."""

    def __init__(self, ctx, account, symbol):
        check_order_host(ctx["base"])
        self.ctx, self.account, self.symbol = ctx, account, symbol

    def pull(self, what):
        """Read-only records: 'positions', 'live' (orders/live)."""
        path = {"positions": f"/accounts/{self.account}/positions", "live": f"/accounts/{self.account}/orders/live"}[what]
        return try_call(self.ctx, "GET", path)

    def send(self, intent, on_submit=lambda: None):
        """intent: dict(role, action 'Buy'/'Sell', quantity, external_identifier, ... shown beside it, never read).
        Returns dict(sent, reason, outcome). Writes the intent line, the outcome line and a line per fill."""
        ctx, acct, ext = self.ctx, self.account, intent["external_identifier"]
        rec = ctx["rec"]
        check_order_host(ctx["base"])
        if in_halt_hour():
            write_intent(rec, intent, False, "wall clock 5 pm to 6 pm Eastern: no order of any kind (sandbox refuses futures)")
            return {"sent": False, "reason": "halt hour", "outcome": None}
        try:
            mint(ctx)   # a fresh token before every submit
            # copied from tasty/tasty_7e_check.py lines 389-409 (the dry-run body and its error / warning test)
            leg = {"instrument-type": "Future", "symbol": self.symbol, "action": intent["action"],
                   "quantity": intent["quantity"]}
            body = {"time-in-force": ORDER_TIME_IN_FORCE, "order-type": ORDER_TYPE}
            body["legs"] = [leg]
            _, obj = http(ctx, "POST", f"/accounts/{acct}/orders/dry-run", body)
            found = {}
            for where, src in (("top", obj), ("data", dget(obj, "data"))):
                for key in ("errors", "warnings"):
                    if dget(src, key):
                        found[f"{where}.{key}"] = src[key]
            if found:
                rec.note("dry-run returned errors or warnings (a warning means the order would be rejected): "
                         "nothing submitted. " + json.dumps(found))
                write_intent(rec, intent, False, "dry-run returned errors or warnings: " + ", ".join(found))
                return {"sent": False, "reason": "dry-run errors or warnings", "outcome": None}
        except (StepStop, Uncertain) as e:
            write_intent(rec, intent, False, f"before the submit: {scrub(str(e))}")
            return {"sent": False, "reason": f"before the submit: {scrub(str(e))}", "outcome": None}

        write_intent(rec, intent, True, "dry-run clean")
        on_submit()
        submit_body = dict(body, **{"external-identifier": ext})   # copied from 7e line 415
        oid, lookup, order_obj = None, None, None
        try:
            _, obj = http(ctx, "POST", f"/accounts/{acct}/orders", submit_body, retry=False)   # 7e line 417
            oid = dget(obj, "data", "order", "id")
            order_obj = dget(obj, "data", "order")
            if oid is None:
                # copied from 7e lines 424-430: a 2xx without the id where we look is as uncertain as a timeout
                rec.note("submit response has no data.order.id where the code looks for it; checking for our "
                         "external-identifier; not polling by id")
                lookup, order_obj = check_uncertain_submit(ctx, acct, ext, LOOKUP_MAX_PAGES)
        except Uncertain as e:
            # copied from 7e lines 418-422: never resubmit, on any outcome but a clean 2xx
            rec.note(f"submit result uncertain ({e}); NOT resubmitting; checking for our external-identifier")
            try:
                lookup, order_obj = check_uncertain_submit(ctx, acct, ext, LOOKUP_MAX_PAGES)
            except (StepStop, Uncertain) as e2:
                lookup = {"look-up": f"could not run: {scrub(str(e2))}"}
        if oid is None and order_obj is not None:
            oid = order_obj.get("id")
        outcome = read_outcome(order_obj) if order_obj else {"order_id": oid, "status_as_said": None, "fills": [],
                                                              "filled_quantity": 0, "confirmed": False}
        polls = 0
        if oid is not None:      # poll the order until its fills are there or it is terminal (7e lines 433-448)
            for i in range(POLL_COUNT):
                if outcome["confirmed"]:
                    break
                if i:
                    time.sleep(POLL_SECONDS)
                try:
                    _, o = http(ctx, "GET", f"/accounts/{acct}/orders/{oid}")
                except StepStop as e:      # a failed poll is recorded; go on to the next
                    ctx["failed"].append({"step": f"poll {i + 1}", "note": scrub(str(e))})
                    rec.note(f"poll {i + 1} failed: {e}")
                    continue
                polls += 1
                outcome = read_outcome(o)
                if str(outcome["status_as_said"]).lower() in TERMINAL:
                    break
        outcome.update(polls=polls, lookup=lookup, order_id=oid if oid is not None else outcome.get("order_id"))
        at = intent.get("event_at") or {}
        for f in outcome["fills"]:       # the price as the sandbox said it, beside the print's bid and offer
            rec.write(t_ns=time.time_ns(), kind="fill", external_identifier=ext, order_id=outcome["order_id"], fill=f,
                      bid_at_print=at.get("bid"), offer_at_print=at.get("offer"))
        rec.write(t_ns=time.time_ns(), kind="outcome", external_identifier=ext, outcome=outcome)
        return {"sent": True, "reason": "submitted", "outcome": outcome}

    def final_read(self, oid):
        """A last read of one order (for the end-of-run pull)."""
        try:
            _, o = http(self.ctx, "GET", f"/accounts/{self.account}/orders/{oid}")
            return read_outcome(o)
        except (StepStop, Uncertain) as e:
            return {"order_id": oid, "error": scrub(str(e))}

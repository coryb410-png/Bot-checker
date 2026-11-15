"""Live, in-memory view of what the server is doing, for the admin panel.

Nothing here is written anywhere: it's gone when the server restarts, like the
scans themselves. No wallet addresses are recorded: events carry networks,
counts, durations and error types only (error text is scrubbed of anything
that looks like an address or key).
"""
from __future__ import annotations

import re
import time
from collections import Counter, deque
from datetime import datetime, timezone

STARTED_AT = datetime.now(timezone.utc)
_started_mono = time.monotonic()

counters: Counter = Counter()          # e.g. "scans_started", "rate_limited:scan", "provider_error:solana"
networks: Counter = Counter()          # scans per network
events: deque = deque(maxlen=300)      # newest last
scan_started: dict[str, float] = {}    # portfolio id -> monotonic start (for durations)

maintenance = {"on": False, "message": "ChainScope is being updated. Please try again in a few minutes."}

# Long hex / base58 / bech32 runs (addresses, hashes, keys) and URL query strings
_SCRUB = re.compile(r"(0x[0-9a-fA-F]{8,}|\b[13a-km-zA-HJ-NP-Z1-9]{25,}\b|\b(?:bc1|addr1|stake1)[0-9a-z]{8,}|\?[^\s\"']+)")


def scrub(text: str | None, limit: int = 200) -> str:
    return _SCRUB.sub("…", (text or ""))[:limit]


def uptime_seconds() -> int:
    return int(time.monotonic() - _started_mono)


_LOUD = {"provider_error", "scan_failed", "health"}


def record(kind: str, message: str, **data) -> None:
    counters[kind] += 1
    if kind in _LOUD:   # also to the server log (already scrubbed of addresses), for Render's log view
        print(f"[chainscope] {kind}: {scrub(message, 400)}", flush=True)
    events.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
                   "message": scrub(message, 240), **{k: v for k, v in data.items() if v is not None}})


def scan_begun(pid: str, chains: list[str]) -> None:
    scan_started[pid] = time.monotonic()
    networks.update(chains)
    _bump_hour("started")
    record("scan_started", f"Scan started on {len(chains)} network(s)", networks=sorted(set(chains)))


def scan_ended(pid: str, ok: bool, txs: int = 0, error: str | None = None) -> None:
    secs = round(time.monotonic() - scan_started.pop(pid, time.monotonic()), 1)
    _bump_hour("finished" if ok else "failed")
    if ok:
        record("scan_finished", f"Scan finished: {txs} transactions in {secs}s", seconds=secs)
    else:
        record("scan_failed", f"Scan failed after {secs}s: {scrub(error)}", seconds=secs)


def provider_error(chain: str, error: str) -> None:
    counters[f"provider_error:{chain}"] += 1
    record("provider_error", f"{chain}: {scrub(error)}", network=chain)


def rate_limited(kind: str) -> None:
    counters[f"rate_limited:{kind}"] += 1
    if kind != "request" or counters[f"rate_limited:{kind}"] % 25 == 1:   # don't flood the log
        record("rate_limited", f"Someone hit the {kind} limit")


# ------------------------------------------------------------ usage charts
HOUR = 3600
hourly: dict[int, Counter] = {}                # hour number -> started/finished/failed
net_times: dict[str, deque] = {}               # network -> recent scan durations (seconds)
net_fails: Counter = Counter()


def _bump_hour(kind: str) -> None:
    h = int(time.time() // HOUR)
    hourly.setdefault(h, Counter())[kind] += 1
    for old in [k for k in hourly if k < h - 24]:
        del hourly[old]


def network_timed(chain: str, seconds: float, ok: bool) -> None:
    net_times.setdefault(chain, deque(maxlen=200)).append(round(seconds, 2))
    if not ok:
        net_fails[chain] += 1


def usage() -> dict:
    h = int(time.time() // HOUR)
    hours = []
    for k in range(h - 23, h + 1):
        c = hourly.get(k, Counter())
        hours.append({"hour": datetime.fromtimestamp(k * HOUR, timezone.utc).isoformat(timespec="minutes"),
                      "started": c["started"], "finished": c["finished"], "failed": c["failed"]})
    nets = []
    for name, scans in networks.most_common():
        t = sorted(net_times.get(name, []))
        nets.append({"network": name, "scans": scans, "errors": net_fails[name],
                     "avg_seconds": round(sum(t) / len(t), 1) if t else None,
                     "slowest_seconds": t[-1] if t else None,
                     "typical_seconds": t[len(t) // 2] if t else None})
    return {"hours": hours, "networks": nets}


# ------------------------------------------------------------ provider health history
health_history: dict[str, deque] = {}          # provider -> (unix time, ok, ms), ~24h at 5-minute checks
health_last: dict = {"at": None, "results": []}


def health_ran(results: list[dict]) -> None:
    now = time.time()
    for r in results:
        health_history.setdefault(r["name"], deque(maxlen=400)).append((now, bool(r["ok"]), r.get("ms")))
    health_last["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    health_last["results"] = results
    down = [r for r in results if not r["ok"]]
    if down:
        record("health", "Health check: not working: " + "; ".join(f"{r['name']} ({r.get('error') or 'failed'})" for r in down))


def health_summary() -> list[dict]:
    """Per provider over the last 24 hours: share of good checks, outages, recent dots."""
    cutoff = time.time() - 24 * HOUR
    last = {r["name"]: r for r in health_last["results"]}
    out = []
    for name, hist in health_history.items():
        recent = [h for h in hist if h[0] >= cutoff]
        if not recent:
            continue
        outages, prev = 0, True
        for _, ok, _ms in recent:
            if prev and not ok:
                outages += 1
            prev = ok
        oks = [h for h in recent if h[1]]
        r = last.get(name, {})
        out.append({"name": name, "ok": r.get("ok", recent[-1][1]), "ms": r.get("ms"), "error": r.get("error"),
                    "uptime": round(100 * len(oks) / len(recent), 1), "checks": len(recent), "outages": outages,
                    "recent": [1 if h[1] else 0 for h in recent[-48:]]})
    return out


# ------------------------------------------------------------ network switches
disabled_networks: set[str] = set()


# ------------------------------------------------------------ visitors and blocks
# Visitors are known only by a scrambled ID (never their IP), counted over
# roughly the last hour, and forgotten after that.
visitors: dict[str, dict] = {}
blocked: dict[str, dict] = {}                  # id -> {"until": unix time, "label": str}
_visitor_sweep = [time.time()]


def seen(vid: str, label: str, kind: str, limited: bool = False) -> None:
    now = time.time()
    v = visitors.get(vid)
    if v is None or now - v["since"] > HOUR:
        v = visitors[vid] = {"label": label, "since": now, "requests": 0, "scans": 0, "limited": 0, "last": now}
    v["last"] = now
    if limited:
        v["limited"] += 1
    elif kind == "scan":
        v["scans"] += 1
    else:
        v["requests"] += 1
    if now - _visitor_sweep[0] > 300:
        _visitor_sweep[0] = now
        for k in [k for k, x in visitors.items() if now - x["last"] > HOUR]:
            del visitors[k]


def is_blocked(vid: str) -> bool:
    b = blocked.get(vid)
    if b and b["until"] <= time.time():
        del blocked[vid]
        return False
    return b is not None


def top_visitors(n: int = 25) -> list[dict]:
    now = time.time()
    rows = [{"id": k, "label": v["label"], "requests": v["requests"], "scans": v["scans"], "limited": v["limited"],
             "last_seen_seconds": int(now - v["last"]), "blocked": is_blocked(k)}
            for k, v in visitors.items() if now - v["last"] <= HOUR]
    rows.sort(key=lambda r: (r["limited"], r["scans"], r["requests"]), reverse=True)
    return rows[:n]


def blocks() -> list[dict]:
    now = time.time()
    return [{"id": k, "label": b["label"], "minutes_left": max(1, int((b["until"] - now) // 60))}
            for k, b in list(blocked.items()) if is_blocked(k)]

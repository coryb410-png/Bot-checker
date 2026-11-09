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


def record(kind: str, message: str, **data) -> None:
    counters[kind] += 1
    events.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind,
                   "message": scrub(message, 240), **{k: v for k, v in data.items() if v is not None}})


def scan_begun(pid: str, chains: list[str]) -> None:
    scan_started[pid] = time.monotonic()
    networks.update(chains)
    record("scan_started", f"Scan started on {len(chains)} network(s)", networks=sorted(set(chains)))


def scan_ended(pid: str, ok: bool, txs: int = 0, error: str | None = None) -> None:
    secs = round(time.monotonic() - scan_started.pop(pid, time.monotonic()), 1)
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

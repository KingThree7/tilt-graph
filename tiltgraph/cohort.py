"""Build a cohort: many public wallets in one store, so one trader can be read against peers.

Wallets come from Hyperliquid's public leaderboard file. We keep small, human-sized accounts
and drop anything that looks like a bot or market maker. In the store a wallet is known only
by an alias; the address itself is never written.
"""
import hashlib
import json
import os
import random
import time
import urllib.request

from . import hl

LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"


def leaderboard(path=None):
    if path and os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)["leaderboardRows"]
    with urllib.request.urlopen(LEADERBOARD, timeout=120) as resp:
        return json.load(resp)["leaderboardRows"]


def pick(rows, n, seed=11, min_value=300, max_value=50_000, min_volume=20_000, max_turnover=400):
    """Human-sized accounts: some money, some activity, not turning the account over all day."""
    keep = []
    for r in rows:
        value = float(r["accountValue"])
        month = dict(r["windowPerformances"]).get("month", {})
        volume = float(month.get("vlm", 0))
        if not (min_value <= value <= max_value) or volume < min_volume:
            continue
        if volume / value > max_turnover:
            continue
        keep.append(r["ethAddress"])
    keep.sort()
    random.Random(seed).shuffle(keep)
    return keep[:n]


def alias(address, salt):
    return "w_" + hashlib.sha256((salt + address.lower()).encode()).hexdigest()[:8]


def fetch(addresses, start_ms, cache_dir, pause=1.1, max_pages=3, log=print):
    """Download fills per wallet into cache_dir (one json per wallet). Resumable."""
    os.makedirs(cache_dir, exist_ok=True)
    for i, address in enumerate(addresses):
        target = os.path.join(cache_dir, address + ".json")
        if os.path.exists(target):
            continue
        try:
            fills = hl.fetch_fills(address, start_ms, max_pages=max_pages, pause=pause)
        except Exception as err:  # one bad wallet must not stop the run
            log(f"{i} {address[:8]} failed: {err}")
            time.sleep(10)
            continue
        with open(target, "w") as fh:
            json.dump(fills, fh)
        if i % 25 == 0:
            log(f"{i}/{len(addresses)} wallets, last had {len(fills)} fills")


def build(conn, cache_dir, salt, min_positions=20, max_fills=5500, window=15, size_up=1.5, log=print):
    """Fold every cached wallet into positions and write them all into one store."""
    from . import graph, positions as pos
    kept = dropped = total = 0
    for name in sorted(os.listdir(cache_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(cache_dir, name)) as fh:
            fills = json.load(fh)
        if len(fills) >= max_fills:
            dropped += 1  # that many fills in the window is a machine, not a person
            continue
        key = alias(name[:-5], salt)
        closed = pos.build_positions(key, fills)
        if len(closed) < min_positions:
            dropped += 1
            continue
        graph.write(conn, key, closed, derive_now=False)
        kept += 1
        total += len(closed)
    out = {"wallets": kept, "dropped": dropped, "positions": total}
    out.update(graph.derive(conn, window=window, size_up=size_up))
    return out

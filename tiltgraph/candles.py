"""How far did the price move against a position while it was open? Read from candles.

That number, Position.worst_move_pct, is what makes the fair test of "adding under water"
possible: it says which positions faced the same decision.
"""
import bisect
import json
import os
import time

from . import hl

INTERVAL = "15m"


def _file(folder, coin):
    return os.path.join(folder, coin.replace(":", "_").replace("/", "_") + ".json")


def fetch(coins, start_ms, folder, pause=0.6, log=None):
    """Download 15 minute candles per coin into folder. Resumable."""
    os.makedirs(folder, exist_ok=True)
    end = int(time.time() * 1000)
    for coin in coins:
        path = _file(folder, coin)
        if os.path.exists(path):
            continue
        try:
            rows = _candles(coin, INTERVAL, start_ms, end, pause)
            # The venue keeps only the latest 5,000 candles of an interval (52 days at 15 minutes).
            # Older than that, fall back to hourly candles so the whole window is covered.
            first = rows[0][0] if rows else end
            if first - start_ms > 86400_000:
                rows = [r for r in _candles(coin, "1h", start_ms, first, pause)
                        if r[0] + 3600_000 <= first] + rows
        except Exception as err:  # a coin without candles must not stop the run
            if log:
                log(f"{coin}: {err}")
            continue
        with open(path, "w") as fh:
            json.dump(rows, fh)


def _candles(coin, interval, start_ms, end_ms, pause):
    rows, cursor = [], start_ms
    for _ in range(6):
        page = hl._post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval, "startTime": cursor, "endTime": end_ms}}) or []
        time.sleep(pause)
        rows += [[c["t"], float(c["h"]), float(c["l"])] for c in page]
        if len(page) < 4900 or page[-1]["t"] + 1 <= cursor:
            break
        cursor = page[-1]["t"] + 1
    return sorted(rows)


def load(folder):
    """{coin file name: (times, rows)} with rows of [start ms, high, low, ...]."""
    out = {}
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name)) as fh:
            rows = sorted(json.load(fh))
        out[name[:-5]] = ([r[0] for r in rows], rows)
    return out


def from_fills(fills):
    """Stand-in candles made of the fills themselves: every fill is one price sighting.

    Good enough for the made-up example and for tests, where no market data exists.
    """
    by_coin = {}
    for f in fills:
        by_coin.setdefault(f["coin"], []).append([f["time"], float(f["px"]), float(f["px"])])
    return {coin.replace(":", "_").replace("/", "_"): ([r[0] for r in sorted(rows)], sorted(rows))
            for coin, rows in by_coin.items()}


def worst_move(candles, p):
    """Largest move against the entry in %, from the first candle after the open up to the close."""
    series = candles.get(p.coin.replace(":", "_").replace("/", "_"))
    if series is None or not p.entry_px:
        return None
    times, rows = series
    i = bisect.bisect_right(times, p.opened_at)
    j = bisect.bisect_right(times, p.closed_at)
    if i >= j:
        return None
    if p.side == "long":
        move = 100 * (p.entry_px - min(r[2] for r in rows[i:j])) / p.entry_px
    else:
        move = 100 * (max(r[1] for r in rows[i:j]) - p.entry_px) / p.entry_px
    first_add = p.ladder[0][3] if p.ladder else 0.0
    return round(max(0.0, move, first_add), 3)


def worst_moves(candles, positions):
    out = {}
    for p in positions:
        w = worst_move(candles, p)
        if w is not None:
            out[p.id] = w
    return out

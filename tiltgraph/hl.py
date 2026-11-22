"""Read public fills of one Hyperliquid address. No key needed: fills are public."""
import json
import time
import urllib.request

API = "https://api.hyperliquid.xyz/info"
PAGE = 2000  # the API returns at most 2000 fills per call


def _post(body, retries=4):
    data = json.dumps(body).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(API, data=data, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def fetch_fills(address, start_ms, end_ms=None, max_pages=60, pause=0.0):
    """All fills from start_ms on, oldest first, paged by time."""
    fills, seen, cursor = [], set(), start_ms
    for _ in range(max_pages):
        body = {"type": "userFillsByTime", "user": address, "startTime": cursor}
        if end_ms:
            body["endTime"] = end_ms
        page = _post(body) or []
        if pause:
            time.sleep(pause)
        fresh = [f for f in page if f["tid"] not in seen]
        for f in fresh:
            seen.add(f["tid"])
        fills.extend(fresh)
        if len(page) < PAGE or not fresh:
            break
        cursor = max(f["time"] for f in page)
    fills.sort(key=lambda f: (f["time"], f["tid"]))
    return fills


def open_position(address, coin):
    """The address's open position on a coin right now, or None. Public, like the fills."""
    state = _post({"type": "clearinghouseState", "user": address}) or {}
    for item in state.get("assetPositions", []):
        p = item.get("position", {})
        if p.get("coin") == coin and float(p.get("szi", 0)) != 0:
            size = float(p["szi"])
            return {"coin": coin, "side": "long" if size > 0 else "short", "size": abs(size),
                    "entry_px": float(p["entryPx"]), "unrealized_pnl": float(p["unrealizedPnl"]),
                    "liquidation_px": float(p["liquidationPx"]) if p.get("liquidationPx") else None}
    return None


def would_average_down(address, coin, side):
    """True when a new order on this coin and side adds to an open position that is under water."""
    pos = open_position(address, coin)
    return bool(pos and pos["side"] == side and pos["unrealized_pnl"] < 0), pos

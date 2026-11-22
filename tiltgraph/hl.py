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


def fetch_fills(address, start_ms, end_ms=None, max_pages=60):
    """All fills from start_ms on, oldest first, paged by time."""
    fills, seen, cursor = [], set(), start_ms
    for _ in range(max_pages):
        body = {"type": "userFillsByTime", "user": address, "startTime": cursor}
        if end_ms:
            body["endTime"] = end_ms
        page = _post(body) or []
        fresh = [f for f in page if f["tid"] not in seen]
        for f in fresh:
            seen.add(f["tid"])
        fills.extend(fresh)
        if len(page) < PAGE or not fresh:
            break
        cursor = max(f["time"] for f in page)
    fills.sort(key=lambda f: (f["time"], f["tid"]))
    return fills

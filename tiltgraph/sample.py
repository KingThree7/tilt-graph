"""A made-up trader with a planted habit, in the exact shape of Hyperliquid fills.

Why made up: the bundled example should not point at a real person, and tests need a
known truth. The trader below is calm most of the time. After a loss he sometimes
re-enters within minutes, larger, and those trades are worse on purpose. The report
has to find that again from the fills alone.
"""
import random

ADDRESS = "0x000000000000000000000000000000000000d3m0"
COINS = {"BTC": 60000.0, "ETH": 3000.0, "SOL": 150.0, "HYPE": 40.0}


def make_fills(seed=7, days=60, start_ms=1_790_000_000_000):
    rng = random.Random(seed)
    fills, tid = [], 1
    t = start_ms
    end = start_ms + days * 86_400_000
    last_lost, last_coin, last_side, last_size = False, None, None, 1.0

    def fill(coin, side, px, sz, start_pos, closed_pnl, when, opening):
        nonlocal tid
        long_side = (side == "B") == opening
        fills.append({
            "coin": coin, "px": f"{px:.4f}", "sz": f"{sz:.6f}", "side": side, "time": when,
            "startPosition": f"{start_pos:.6f}",
            "dir": ("Open " if opening else "Close ") + ("Long" if long_side else "Short"),
            "closedPnl": f"{closed_pnl:.4f}", "hash": f"0x{tid:064x}", "oid": tid,
            "crossed": rng.random() < 0.6, "fee": f"{px * sz * 0.00035:.6f}",
            "tid": tid, "feeToken": "USDC"})
        tid += 1

    while t < end:
        tilted = last_lost and rng.random() < 0.55
        if tilted:
            t += int(rng.uniform(1, 12) * 60_000)          # back in within minutes
            coin = last_coin if rng.random() < 0.7 else rng.choice(list(COINS))
            size_mult = last_size * rng.uniform(1.4, 2.2)   # and larger
            win_p, hold = 0.30, rng.uniform(3, 25)
            if coin == last_coin and rng.random() < 0.5:
                long = last_side != "long"                  # flip
            else:
                long = last_side == "long"                  # chase
        else:
            t += int(rng.uniform(90, 900) * 60_000)         # hours of calm
            coin = rng.choice(list(COINS))
            size_mult = rng.uniform(0.8, 1.2)
            win_p, hold = 0.55, rng.uniform(30, 480)
            long = rng.random() < 0.5
        if t >= end:
            break
        px = COINS[coin] * rng.uniform(0.9, 1.1)
        sz = round(1000.0 * size_mult / px, 6)
        won = rng.random() < win_p
        move = rng.uniform(0.004, 0.02) * (1 if won else -1)
        exit_px = px * (1 + move if long else 1 - move)
        pnl = (exit_px - px) * sz * (1 if long else -1)
        signed = sz if long else -sz
        open_side, close_side = ("B", "A") if long else ("A", "B")
        fill(coin, open_side, px, sz, 0.0, 0.0, t, True)
        t += int(hold * 60_000)
        fill(coin, close_side, exit_px, sz, signed, pnl, t, False)
        last_lost, last_coin = not won, coin
        last_side, last_size = ("long" if long else "short"), min(size_mult, 6.0)
    return fills

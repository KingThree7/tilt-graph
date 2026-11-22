"""A made-up trader with a planted habit, in the exact shape of Hyperliquid fills.

Why made up: the bundled example should not point at a real person, and tests need a
known truth. The trader below is calm most of the time. Two habits are planted on purpose:
after a loss he sometimes re-enters within minutes, larger; and he sometimes adds to a
position that has moved against him. Both are made worse than his calm trades, the second
one much worse. The report has to find that again from the fills alone.
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

    def fill(coin, side, px, sz, start_pos, closed_pnl, when, opening, liquidated=False):
        nonlocal tid
        long_side = (side == "B") == opening
        fills.append({
            "coin": coin, "px": f"{px:.4f}", "sz": f"{sz:.6f}", "side": side, "time": when,
            "startPosition": f"{start_pos:.6f}",
            "dir": ("Open " if opening else "Close ") + ("Long" if long_side else "Short"),
            "closedPnl": f"{closed_pnl:.4f}", "hash": f"0x{tid:064x}", "oid": tid,
            "crossed": rng.random() < 0.6, "fee": f"{px * sz * 0.00035:.6f}",
            "tid": tid, "feeToken": "USDC"})
        if liquidated:
            fills[-1]["liquidation"] = {"liquidatedUser": ADDRESS, "method": "market"}
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
        sign = 1 if long else -1
        open_side, close_side = ("B", "A") if long else ("A", "B")
        fill(coin, open_side, px, sz, 0.0, 0.0, t, True)
        held, cost, liquidated = sz, px * sz, False
        if not tilted and rng.random() < 0.3:               # averaging down: the costly habit
            win_p = 0.38
            for step in range(rng.randint(1, 4)):
                t += int(rng.uniform(20, 240) * 60_000)
                add_px = px * (1 - sign * 0.012 * (step + 1))  # each add further against him
                add_sz = round(sz * rng.uniform(0.8, 1.3), 6)
                fill(coin, open_side, add_px, add_sz, sign * held, 0.0, t, True)
                held, cost = round(held + add_sz, 6), cost + add_px * add_sz
        avg = cost / held
        won = rng.random() < win_p
        if held > sz and not won:
            move = -rng.uniform(0.03, 0.07)                 # the ladder loses big when it loses
            liquidated = rng.random() < 0.25
        else:
            move = rng.uniform(0.004, 0.02) * (1 if won else -1)
        exit_px = avg * (1 + sign * move)
        pnl = (exit_px - avg) * held * sign
        t += int(hold * 60_000)
        fill(coin, close_side, exit_px, held, sign * held, pnl, t, False, liquidated)
        last_lost, last_coin = not won, coin
        last_side, last_size = ("long" if long else "short"), min(size_mult, 6.0)
    return fills

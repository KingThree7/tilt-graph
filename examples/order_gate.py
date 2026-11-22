"""An order gate: the check an agent runs before it would place a trade.

    python examples/order_gate.py examples/demo.rl --coin SOL --side long --notional 900 --adding-to-loser

It opens the store read-only, asks the guard, and prints what it would do. It never places an
order. Wire your own exchange call in where the comment says so, and only on GO.
"""
import argparse
import json
import sys
import time

from tiltgraph import graph, guard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("database")
    ap.add_argument("--wallet")
    ap.add_argument("--coin", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--notional", type=float, required=True)
    ap.add_argument("--adding-to-loser", action="store_true")
    ap.add_argument("--at", type=int, help="ms since epoch (default: now)")
    a = ap.parse_args()

    conn = graph.open_store(a.database, read_only=True)
    answer = guard.check(conn, a.at or int(time.time() * 1000), wallet=a.wallet, coin=a.coin,
                         side=a.side, notional=a.notional, adding_to_loser=a.adding_to_loser)
    conn.close()

    print(f"{answer['verdict']}: {answer['reason']}")
    if answer["verdict"] == "GO":
        print(f"would place: {a.side} {a.coin} for {a.notional:.0f} USDC")
        # place_order(...) goes here. This example stops before that on purpose.
        return 0
    print("order held back. The facts behind it:")
    print(json.dumps(answer.get("planned_trade_matches", []), indent=1))
    return 2 if answer["verdict"] == "WAIT" else 3


if __name__ == "__main__":
    sys.exit(main())

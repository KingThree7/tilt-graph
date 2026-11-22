"""Command line: python -m tiltgraph ingest|report|ask ..."""
import argparse
import json
import os
import sys
import time

from . import candles, cohort, graph, guard, hl, positions, report, sample, study, view


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tiltgraph", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ingest", help="read public fills of an address into a Relate store")
    a.add_argument("address")
    a.add_argument("-d", "--database", default="tilt.rl")
    a.add_argument("--days", type=int, default=90)
    a.add_argument("--window", type=float, default=15, help="minutes that count as 'right after'")
    a.add_argument("--size-up", type=float, default=1.5)
    a.add_argument("--fills", help="read fills from a JSON file instead of the network")
    a.add_argument("--cache", default=".tilt-cache", help="folder for downloaded candles")

    r = sub.add_parser("report", help="print the report as markdown")
    r.add_argument("-d", "--database", default="tilt.rl")
    r.add_argument("--queries", action="store_true", help="show the Cypher under each table")

    g = sub.add_parser("guard", help="before the next trade: what state am I in, how did that go before?")
    g.add_argument("-d", "--database", default="tilt.rl")
    g.add_argument("--at", type=int, help="moment to judge, in ms since epoch (default: now)")
    g.add_argument("--wallet", help="needed when the store holds several wallets")
    g.add_argument("--coin")
    g.add_argument("--side", choices=["long", "short"])
    g.add_argument("--notional", type=float, help="size of the planned trade in USDC")
    g.add_argument("--adding-to-loser", action="store_true",
                   help="the planned trade adds to an open position that is under water")

    g.add_argument("--live", action="store_true",
                   help="look up the wallet's open position on Hyperliquid to see whether this "
                        "trade would add to a loser (the wallet must be a real address)")

    e = sub.add_parser("evidence", help="test every pattern wallet by wallet, and on later data")
    e.add_argument("-d", "--database", default="cohort.rl")
    e.add_argument("--export", action="store_true", help="save as the bundled evidence file")

    m = sub.add_parser("serve", help="run as an MCP server over stdio, read-only")
    m.add_argument("-d", "--database", default="tilt.rl")

    c = sub.add_parser("cohort", help="build a store of many public wallets (aliases only)")
    c.add_argument("-d", "--database", default="cohort.rl")
    c.add_argument("--wallets", type=int, default=300)
    c.add_argument("--days", type=int, default=60)
    c.add_argument("--cache", default=".tilt-cache", help="folder for downloaded fills")
    c.add_argument("--seed", type=int, default=11)

    s = sub.add_parser("sample", help="build the bundled example store from a made-up trader")
    s.add_argument("-d", "--database", default="examples/demo.rl")

    v = sub.add_parser("view", help="write a self-contained HTML page of the report")
    v.add_argument("-d", "--database", default="tilt.rl")
    v.add_argument("-o", "--output", default="tilt.html")
    v.add_argument("--wallet", help="which wallet to show in detail")

    q = sub.add_parser("ask", help="run one question, print JSON")
    q.add_argument("name", choices=sorted(report.ALL))
    q.add_argument("-d", "--database", default="tilt.rl")

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from . import server
        return server.serve(args.database)
    if args.cmd == "sample" and os.path.exists(args.database):
        os.remove(args.database)  # the example is rebuilt from nothing, never patched
    conn = graph.open_store(args.database, read_only=args.cmd in ("report", "guard", "view", "ask"))
    try:
        if args.cmd == "ingest":
            address = args.address.lower()
            if args.fills:
                with open(args.fills) as fh:
                    fills = json.load(fh)
            else:
                start = int((time.time() - args.days * 86400) * 1000)
                fills = hl.fetch_fills(address, start)
            pos = positions.build_positions(address, fills)
            series = candles.from_fills(fills)
            if not args.fills:  # real candles, so the worst move of every position is known
                folder = os.path.join(args.cache, "candles")
                candles.fetch(sorted({p.coin for p in pos}), start, folder)
                series = candles.load(folder)
            stats = graph.write(conn, address, pos, window=args.window, size_up=args.size_up,
                                worst=candles.worst_moves(series, pos))
            print(json.dumps({"fills": len(fills), **stats, "store": args.database}))
        elif args.cmd == "guard":
            adding, open_pos = args.adding_to_loser, None
            if args.live and args.coin and args.side:
                wallet = guard._wallet(conn, args.wallet)
                adding, open_pos = hl.would_average_down(wallet, args.coin, args.side)
            out = guard.check(conn, args.at or int(time.time() * 1000), args.wallet,
                              args.coin, args.side, args.notional, adding)
            if open_pos:
                out["open_position"] = open_pos
            print(json.dumps(out, indent=1))
        elif args.cmd == "evidence":
            rows = study.evidence(conn)
            audit = study.audit(conn)
            if args.export:
                study.export(conn)
            print(report.table(rows))
            print(report.table(study.under_water(conn)))
            print("Rule learned on the first half of every wallet:", audit["rule_learned_on_first_half"])
            print(report.table(audit["result_on_second_half"]))
        elif args.cmd == "cohort":
            import secrets
            addresses = cohort.pick(cohort.leaderboard(), args.wallets, seed=args.seed)
            start = int((time.time() - args.days * 86400) * 1000)
            fills_dir, candle_dir = os.path.join(args.cache, "fills"), os.path.join(args.cache, "candles")
            cohort.fetch(addresses, start, fills_dir, log=lambda m: print(m, file=sys.stderr))
            candles.fetch(cohort.coins(fills_dir), start, candle_dir)
            stats = cohort.build(conn, fills_dir, secrets.token_hex(8), candle_dir=candle_dir)
            print(json.dumps({**stats, "store": args.database}))
        elif args.cmd == "sample":
            fills = sample.make_fills()
            pos = positions.build_positions(sample.ADDRESS, fills)
            stats = graph.write(conn, sample.ADDRESS, pos,
                                worst=candles.worst_moves(candles.from_fills(fills), pos))
            print(json.dumps({**stats, "store": args.database}))
        elif args.cmd == "view":
            with open(args.output, "w") as fh:
                fh.write(view.html(conn, args.wallet))
            print(args.output)
        elif args.cmd == "report":
            row = conn.execute("MATCH (w:Wallet) RETURN w.address AS a LIMIT 1").fetchone()
            print(report.markdown(conn, row["a"] if row else "?", with_queries=args.queries))
        else:
            title, _, rows = report.run(conn, args.name)
            print(json.dumps({"question": title, "rows": rows}, default=str, indent=1))
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

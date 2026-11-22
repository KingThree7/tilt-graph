"""Command line: python -m tiltgraph ingest|report|ask ..."""
import argparse
import json
import sys
import time

from . import cohort, graph, guard, hl, positions, report, sample, view


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

    q = sub.add_parser("ask", help="run one question, print JSON")
    q.add_argument("name", choices=sorted(report.ALL))
    q.add_argument("-d", "--database", default="tilt.rl")

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        from . import server
        return server.serve(args.database)
    conn = graph.open_store(args.database)
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
            stats = graph.write(conn, address, pos, window=args.window, size_up=args.size_up)
            print(json.dumps({"fills": len(fills), **stats, "store": args.database}))
        elif args.cmd == "guard":
            print(json.dumps(guard.check(conn, args.at or int(time.time() * 1000), args.wallet,
                                         args.coin, args.side, args.notional), indent=1))
        elif args.cmd == "cohort":
            import secrets
            addresses = cohort.pick(cohort.leaderboard(), args.wallets, seed=args.seed)
            start = int((time.time() - args.days * 86400) * 1000)
            cohort.fetch(addresses, start, args.cache, log=lambda m: print(m, file=sys.stderr))
            stats = cohort.build(conn, args.cache, secrets.token_hex(8))
            print(json.dumps({**stats, "store": args.database}))
        elif args.cmd == "sample":
            pos = positions.build_positions(sample.ADDRESS, sample.make_fills())
            stats = graph.write(conn, sample.ADDRESS, pos)
            print(json.dumps({**stats, "store": args.database}))
        elif args.cmd == "view":
            with open(args.output, "w") as fh:
                fh.write(view.html(conn))
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

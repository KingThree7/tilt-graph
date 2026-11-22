"""Command line: python -m tiltgraph ingest|report|ask ..."""
import argparse
import json
import sys
import time

from . import graph, hl, positions, report


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

    q = sub.add_parser("ask", help="run one question, print JSON")
    q.add_argument("name", choices=sorted(report.QUESTIONS))
    q.add_argument("-d", "--database", default="tilt.rl")

    args = ap.parse_args(argv)
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

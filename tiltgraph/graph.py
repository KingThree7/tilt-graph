"""Write positions into a Relate store and connect them.

Shape of the graph:

  (:Wallet)-[:TRADED]->(:Position)-[:ON]->(:Coin)
  (:Position)-[:ON_DAY]->(:Day)
  (:Position)-[:NEXT]->(:Position)                  next position by open time
  (:Position)-[:OPENED_AFTER {gap_min}]->(:Position) the last position that had closed
                                                     before this one opened
  (:Position)-[:SHOWS]->(:Pattern)                  revenge, size_up, flip, chase

The OPENED_AFTER chain is the point: a number in a table says "you lost".
The chain says "you lost, and four minutes later you did this".
"""
import datetime as dt

import relate

SCHEMA = [
    "CREATE CONSTRAINT position_id IF NOT EXISTS FOR (p:Position) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT coin_name IF NOT EXISTS FOR (c:Coin) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT wallet_address IF NOT EXISTS FOR (w:Wallet) REQUIRE w.address IS UNIQUE",
    "CREATE CONSTRAINT day_date IF NOT EXISTS FOR (d:Day) REQUIRE d.date IS UNIQUE",
    "CREATE CONSTRAINT pattern_name IF NOT EXISTS FOR (x:Pattern) REQUIRE x.name IS UNIQUE",
]

PATTERNS = {
    "revenge": "Opened within {window} minutes after closing a loss.",
    "size_up": "Opened after a loss with a position at least {size_up}x as large.",
    "flip": "Opened the opposite side on the same coin within {window} minutes after a loss.",
    "chase": "Re-entered the same coin and side within {window} minutes after a loss.",
}


def open_store(path):
    conn = relate.connect(path)
    for stmt in SCHEMA:
        conn.execute(stmt)
    return conn


def _day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def write(conn, address, positions, window=15, size_up=1.5, lookback_hours=24):
    """Idempotent: running it again on the same store updates, never duplicates."""
    rows = []
    for p in positions:
        r = p.row()
        r["day"] = _day(p.opened_at)
        r["hour"] = dt.datetime.fromtimestamp(p.opened_at / 1000, dt.timezone.utc).hour
        rows.append(r)

    conn.execute("MERGE (w:Wallet {address: $a})", {"a": address})
    for i in range(0, len(rows), 500):
        conn.execute(
            """
            UNWIND $rows AS r
            MERGE (p:Position {id: r.id})
            SET p.coin = r.coin, p.side = r.side, p.opened_at = r.opened_at,
                p.closed_at = r.closed_at, p.pnl = r.pnl, p.fees = r.fees,
                p.max_notional = r.max_notional, p.fills = r.fills, p.adds = r.adds,
                p.taker_share = r.taker_share, p.minutes = r.minutes, p.won = r.won,
                p.hour = r.hour
            MERGE (c:Coin {name: r.coin})
            MERGE (d:Day {date: r.day})
            MERGE (p)-[:ON]->(c)
            MERGE (p)-[:ON_DAY]->(d)
            WITH p
            MATCH (w:Wallet {address: $a})
            MERGE (w)-[:TRADED]->(p)
            """,
            {"rows": rows[i:i + 500], "a": address})

    # NEXT: order of opening. OPENED_AFTER: what had just closed when this one opened.
    nxt, after = [], []
    by_close = sorted(positions, key=lambda p: p.closed_at)
    j, last_closed = 0, None
    for k, p in enumerate(positions):
        if k:
            nxt.append({"a": positions[k - 1].id, "b": p.id})
        while j < len(by_close) and by_close[j].closed_at <= p.opened_at:
            if by_close[j].id != p.id:
                last_closed = by_close[j]
            j += 1
        if last_closed is not None:
            gap = (p.opened_at - last_closed.closed_at) / 60000
            if gap <= lookback_hours * 60:
                after.append({"a": p.id, "b": last_closed.id, "gap": round(gap, 2),
                              "ratio": round(p.max_notional / last_closed.max_notional, 3)
                              if last_closed.max_notional else None})
    for i in range(0, len(nxt), 1000):
        conn.execute("UNWIND $e AS e MATCH (a:Position {id: e.a}), (b:Position {id: e.b}) "
                     "MERGE (a)-[:NEXT]->(b)", {"e": nxt[i:i + 1000]})
    for i in range(0, len(after), 1000):
        conn.execute("UNWIND $e AS e MATCH (a:Position {id: e.a}), (b:Position {id: e.b}) "
                     "MERGE (a)-[r:OPENED_AFTER]->(b) SET r.gap_min = e.gap, r.size_ratio = e.ratio",
                     {"e": after[i:i + 1000]})

    # Patterns are derived inside the graph, so they can be re-derived with other thresholds.
    conn.execute("MATCH (:Position)-[s:SHOWS]->(:Pattern) DELETE s")
    for name, text in PATTERNS.items():
        conn.execute("MERGE (x:Pattern {name: $n}) SET x.meaning = $m",
                     {"n": name, "m": text.format(window=window, size_up=size_up)})
    tag = "MATCH (x:Pattern {name: $n}) MERGE (p)-[:SHOWS]->(x)"
    base = "MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position) WHERE prev.won = false "
    conn.execute(base + "AND r.gap_min <= $w WITH p " + tag, {"w": window, "n": "revenge"})
    conn.execute(base + "AND r.size_ratio >= $s WITH p " + tag, {"s": size_up, "n": "size_up"})
    conn.execute(base + "AND r.gap_min <= $w AND prev.coin = p.coin AND prev.side <> p.side WITH p " + tag,
                 {"w": window, "n": "flip"})
    conn.execute(base + "AND r.gap_min <= $w AND prev.coin = p.coin AND prev.side = p.side WITH p " + tag,
                 {"w": window, "n": "chase"})
    conn.commit()
    return {"positions": len(rows), "next": len(nxt), "opened_after": len(after)}

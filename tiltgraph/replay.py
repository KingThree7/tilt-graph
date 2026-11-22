"""Walk forward through history and ask: what would the guard have said, each time?

For every position, in the order they were opened, the guard's rule is applied using only
that wallet's positions that had already CLOSED at that moment. Nothing from the future, and
nothing from other wallets. The verdict is written on the position as `guard_said`, so the
question "did the stopped trades really do worse?" becomes one Cypher statement.
"""
from . import graph
from .guard import _z

VERDICT_Q = """
MATCH (p:Position) WHERE p.guard_said IS NOT NULL
RETURN p.guard_said AS guard_said, count(p) AS positions, count(DISTINCT p.wallet) AS wallets,
       round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
       round(1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p), 1) AS liquidated_per_1000,
       round(sum(p.pnl), 0) AS total_pnl
ORDER BY guard_said
"""


def run(conn, min_own=8, gap_pts=8.0, z_max=-1.28):
    rows = conn.execute(
        "MATCH (p:Position)-[:OPENED_IN]->(s:State) "
        "RETURN p.id AS id, p.wallet AS wallet, p.opened_at AS opened_at, p.closed_at AS closed_at, "
        "p.won AS won, s.name AS state ORDER BY p.wallet, p.opened_at").fetchall()
    by_wallet = {}
    for r in rows:
        by_wallet.setdefault(r["wallet"], []).append(r)
    said = []
    for positions in by_wallet.values():
        closed = sorted(positions, key=lambda r: r["closed_at"])
        seen, j = {}, 0  # state -> [positions, wins] among those already closed
        for p in positions:
            while j < len(closed) and closed[j]["closed_at"] <= p["opened_at"]:
                c = seen.setdefault(closed[j]["state"], [0, 0])
                c[0] += 1
                c[1] += 1 if closed[j]["won"] else 0
                j += 1
            here, calm = seen.get(p["state"], [0, 0]), seen.get("calm", [0, 0])
            if p["state"] == "calm":
                verdict = "GO (calm)"
            elif here[0] < min_own or calm[0] < min_own:
                verdict = "UNKNOWN"
            else:
                diff = 100.0 * here[1] / here[0] - 100.0 * calm[1] / calm[0]
                worse = diff <= -gap_pts and _z(here[1], here[0], calm[1], calm[0]) <= z_max
                verdict = "WAIT" if worse else "GO"
            said.append({"id": p["id"], "v": verdict})
    for i in range(0, len(said), 2000):
        conn.execute("UNWIND $rows AS r MATCH (p:Position {id: r.id}) SET p.guard_said = r.v",
                     {"rows": said[i:i + 2000]})
    conn.commit()
    return [dict(r) for r in conn.execute(VERDICT_Q)]

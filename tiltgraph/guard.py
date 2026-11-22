"""The question to ask before the next trade: what state am I in, and how did that go before?

It reads the last closed position, then walks the graph for every earlier moment that
looked the same (after a win or a loss, same waiting time bucket) and reports how the
positions opened in that state turned out.
"""

BUCKETS = [(5, "within 5 min"), (15, "5 to 15 min"), (60, "15 to 60 min"), (10 ** 9, "over 1 hour")]


def _bucket(minutes):
    lo = 0
    for hi, name in BUCKETS:
        if minutes <= hi:
            return lo, hi, name
        lo = hi


def check(conn, now_ms, min_cases=8):
    last = conn.execute(
        "MATCH (p:Position) WHERE p.closed_at <= $now "
        "RETURN p.coin AS coin, p.side AS side, p.pnl AS pnl, p.won AS won, p.closed_at AS closed_at "
        "ORDER BY p.closed_at DESC LIMIT 1", {"now": now_ms}).fetchone()
    if last is None:
        return {"verdict": "NO HISTORY", "reason": "The store holds no closed position before this moment."}
    gap = (now_ms - last["closed_at"]) / 60000
    lo, hi, name = _bucket(gap)
    same = conn.execute(
        """
        MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position)
        WHERE prev.won = $won AND r.gap_min > $lo AND r.gap_min <= $hi
        RETURN count(p) AS n, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins,
               avg(p.pnl) AS avg_pnl, avg(r.size_ratio) AS size_ratio
        """, {"won": last["won"], "lo": lo, "hi": hi}).fetchone()
    base = conn.execute(
        "MATCH (p:Position) RETURN count(p) AS n, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins, "
        "avg(p.pnl) AS avg_pnl").fetchone()
    out = {
        "last_closed": {"coin": last["coin"], "side": last["side"], "pnl": round(last["pnl"], 2),
                        "minutes_ago": round(gap, 1)},
        "state": ("after a win" if last["won"] else "after a loss") + ", " + name,
        "similar_cases": same["n"],
        "baseline": {"positions": base["n"], "win_pct": round(100 * base["wins"] / base["n"], 1),
                     "avg_pnl": round(base["avg_pnl"], 2)},
    }
    if same["n"] < min_cases:
        out["verdict"] = "UNKNOWN"
        out["reason"] = f"Only {same['n']} earlier cases in this state: too few to judge."
        return out
    win_pct = 100 * same["wins"] / same["n"]
    out["in_this_state"] = {"win_pct": round(win_pct, 1), "avg_pnl": round(same["avg_pnl"], 2),
                            "avg_size_vs_previous": round(same["size_ratio"] or 0, 2)}
    worse = same["avg_pnl"] < base["avg_pnl"] and win_pct < out["baseline"]["win_pct"]
    out["verdict"] = "WAIT" if worse else "GO"
    out["reason"] = (
        f"In {same['n']} earlier cases {out['state']}, the next position won {win_pct:.0f}% of the time "
        f"(usual: {out['baseline']['win_pct']}%) with an average result of {same['avg_pnl']:.2f} "
        f"(usual: {base['avg_pnl']:.2f}).")
    return out

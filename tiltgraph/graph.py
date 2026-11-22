"""Write positions into a Relate store and connect them.

Shape of the graph:

  (:Wallet)-[:TRADED]->(:Position)-[:ON]->(:Coin)
  (:Position)-[:OPENED_AFTER {gap_min, size_ratio}]->(:Position)
                                                      the position that had just closed
  (:Position)-[:TILTED_FROM]->(:Position)             opened within 30 min after that loss
  (:Position)-[:OPENED_IN]->(:State)                  the situation it was opened in
  (:Position)-[:SHOWS]->(:Pattern)                    revenge, size_up, flip, chase,
                                                      average_down
  (:Position)-[:AVERAGED_DOWN]->(:Add)-[:THEN]->(:Add)  the ladder of adds against the position
  (:Wallet)-[:HAS_HABIT {positions, share, win_pct, avg_ret, pnl}]->(:Pattern)
  (:Wallet)-[:PEER_OF {score}]->(:Wallet)             nearest wallets by habit vector

The OPENED_AFTER chain is the point: a number in a table says "you lost".
The chain says "you lost, and four minutes later you did this".
"""
import gzip
import os
import shutil
import datetime as dt
import math

import relate

SCHEMA = [
    "CREATE CONSTRAINT position_id IF NOT EXISTS FOR (p:Position) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT coin_name IF NOT EXISTS FOR (c:Coin) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT wallet_address IF NOT EXISTS FOR (w:Wallet) REQUIRE w.address IS UNIQUE",
    "CREATE CONSTRAINT pattern_name IF NOT EXISTS FOR (x:Pattern) REQUIRE x.name IS UNIQUE",
    "CREATE CONSTRAINT state_name IF NOT EXISTS FOR (s:State) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT add_id IF NOT EXISTS FOR (a:Add) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT verdict_name IF NOT EXISTS FOR (v:Verdict) REQUIRE v.name IS UNIQUE",
]

PATTERNS = {
    "revenge": "Opened within {window} minutes after closing a loss.",
    "size_up": "Opened after a loss with a position at least {size_up}x as large.",
    "flip": "Opened the opposite side on the same coin within {window} minutes after a loss.",
    "chase": "Re-entered the same coin and side within {window} minutes after a loss.",
    "average_down": "Added to the position while it was at least 1% under water.",
}

# The situation a position is opened in: what just closed, and how long ago.
# (name, after, low minutes, high minutes). "calm" is the reference: nothing closed for an hour.
STATES = [
    ("after a loss, under 5 min", "loss", 0, 5),
    ("after a loss, 5 to 15 min", "loss", 5, 15),
    ("after a loss, 15 to 60 min", "loss", 15, 60),
    ("after a win, under 5 min", "win", 0, 5),
    ("after a win, 5 to 15 min", "win", 5, 15),
    ("after a win, 15 to 60 min", "win", 15, 60),
    ("calm", "any", 60, None),
]
HABIT_DIM = 8


def _unpack(path):
    """The cohort store ships gzipped. Unpack it next to the archive the first time it is asked for."""
    path = str(path)
    if not os.path.exists(path) and os.path.exists(path + ".gz"):
        with gzip.open(path + ".gz", "rb") as src, open(path + ".part", "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(path + ".part", path)
    return path


def open_store(path, read_only=False):
    path = _unpack(path)
    if read_only:
        return relate.connect(path, read_only=True)
    conn = relate.connect(path)
    for stmt in SCHEMA:
        conn.execute(stmt)
    return conn


def state_name(prev_won, gap_min):
    """Which State a position belongs to. None previous close counts as calm."""
    if prev_won is None or gap_min is None or gap_min >= 60:
        return "calm"
    for name, after, low, high in STATES:
        if after == ("win" if prev_won else "loss") and low <= gap_min < high:
            return name
    return "calm"


def write(conn, address, positions, window=15, size_up=1.5, lookback_hours=24, derive_now=True,
          worst=None):
    """Idempotent: running it again on the same store updates, never duplicates.

    With many wallets, pass derive_now=False and call derive() once at the end.
    worst: {position id: largest move against the entry in %}, from candles, when available.
    """
    rows = []
    for p in positions:
        r = p.row()
        r["first_add_pct"] = p.ladder[0][3] if p.ladder else None
        r["worst_move_pct"] = (worst or {}).get(p.id)
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
                p.hour = r.hour, p.ret = r.ret, p.liquidated = r.liquidated, p.adds_against = r.adds_against, p.wallet = $a,
                p.entry_px = r.entry_px, p.first_add_pct = r.first_add_pct,
                p.worst_move_pct = r.worst_move_pct
            MERGE (c:Coin {name: r.coin})
            MERGE (p)-[:ON]->(c)
            WITH p
            MATCH (w:Wallet {address: $a})
            MERGE (w)-[:TRADED]->(p)
            """,
            {"rows": rows[i:i + 500], "a": address})

    # The ladder: every add made at a worse price than the average entry, in order.
    adds, links = [], []
    for p in positions:
        for step, (at, px, notional, away) in enumerate(p.ladder, 1):
            adds.append({"id": f"{p.id}#{step}", "p": p.id, "at": at, "px": px, "notional": notional,
                         "away": away, "step": step})
            if step > 1:
                links.append({"a": f"{p.id}#{step - 1}", "b": f"{p.id}#{step}"})
    for i in range(0, len(adds), 1000):
        conn.execute("UNWIND $rows AS r MERGE (a:Add {id: r.id}) "
                     "SET a.at = r.at, a.px = r.px, a.notional = r.notional, a.pct_from_entry = r.away, "
                     "    a.step = r.step "
                     "WITH a, r WHERE r.step = 1 MATCH (p:Position {id: r.p}) MERGE (p)-[:AVERAGED_DOWN]->(a)",
                     {"rows": adds[i:i + 1000]})
    for i in range(0, len(links), 1000):
        conn.execute("UNWIND $e AS e MATCH (a:Add {id: e.a}), (b:Add {id: e.b}) MERGE (a)-[:THEN]->(b)",
                     {"e": links[i:i + 1000]})

    # OPENED_AFTER: what had just closed when this one opened.
    after = []
    by_close = sorted(positions, key=lambda p: p.closed_at)
    j, last_closed = 0, None
    for p in positions:
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
    for i in range(0, len(after), 1000):
        conn.execute("UNWIND $e AS e MATCH (a:Position {id: e.a}), (b:Position {id: e.b}) "
                     "MERGE (a)-[r:OPENED_AFTER]->(b) SET r.gap_min = e.gap, r.size_ratio = e.ratio",
                     {"e": after[i:i + 1000]})
    out = {"positions": len(rows), "opened_after": len(after), "ladder_adds": len(adds)}
    if derive_now:
        out.update(derive(conn, window=window, size_up=size_up))
    conn.commit()
    return out


def derive(conn, window=15, size_up=1.5, peers=8, min_positions=20, spiral_gap=30, min_cohort=30):
    """Everything that is computed from the raw chain. Safe to run again with other thresholds."""
    # 1. Patterns on positions.
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

    conn.execute("MATCH (p:Position) WHERE p.adds_against >= 1 WITH p " + tag, {"n": "average_down"})
    conn.execute("MATCH (x:Pattern) WHERE NOT x.name IN $names DETACH DELETE x", {"names": list(PATTERNS)})

    # 1b. TILTED_FROM: the loss-then-quick-reentry link that spirals are made of.
    conn.execute("MATCH (:Position)-[t:TILTED_FROM]->(:Position) DELETE t")
    conn.execute("MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position) "
                 "WHERE prev.won = false AND r.gap_min <= $s "
                 "MERGE (p)-[t:TILTED_FROM]->(prev) SET t.gap_min = r.gap_min", {"s": spiral_gap})

    # 2. States: the situation each position was opened in.
    conn.execute("MATCH (:Position)-[s:OPENED_IN]->(:State) DELETE s")
    for order, (name, after, low, high) in enumerate(STATES):
        conn.execute("MERGE (s:State {name: $n}) SET s.after = $a, s.low_min = $l, s.high_min = $h, s.rank = $o",
                     {"n": name, "a": after, "l": low, "h": high, "o": order})
        if after == "any":
            continue
        conn.execute(
            "MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position) "
            "WHERE prev.won = $won AND r.gap_min >= $l AND r.gap_min < $h "
            "MATCH (s:State {name: $n}) MERGE (p)-[:OPENED_IN]->(s)",
            {"won": after == "win", "l": low, "h": high, "n": name})
    conn.execute("MATCH (p:Position) WHERE NOT (p)-[:OPENED_IN]->(:State) "
                 "MATCH (s:State {name: 'calm'}) MERGE (p)-[:OPENED_IN]->(s)")

    # 3. Each wallet's habits, as edges an agent can walk.
    conn.execute("MATCH (:Wallet)-[h:HAS_HABIT]->(:Pattern) DELETE h")
    conn.execute(
        "MATCH (w:Wallet)-[:TRADED]->(p:Position) "
        "WITH w, count(p) AS n, sum(p.pnl) AS pnl, "
        "     100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p) AS win, "
        "     sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) AS liq "
        "SET w.positions = n, w.pnl = round(pnl, 2), w.win_pct = round(win, 1), w.liquidations = liq")
    conn.execute(
        "MATCH (w:Wallet)-[:TRADED]->(p:Position)-[:SHOWS]->(x:Pattern) "
        "WITH w, x, count(p) AS n, sum(p.pnl) AS pnl, avg(p.ret) AS ret, "
        "     100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p) AS win "
        "MERGE (w)-[h:HAS_HABIT]->(x) "
        "SET h.positions = n, h.share = round(1.0 * n / w.positions, 3), "
        "    h.win_pct = round(win, 1), h.avg_ret = round(ret, 5), h.pnl = round(pnl, 2)")

    # 4. Habit vector per wallet, a vector index over it, and the nearest wallets as PEER_OF.
    made = _habit_vectors(conn, min_positions)
    conn.execute("MATCH (:Wallet)-[e:PEER_OF]->(:Wallet) DELETE e")
    linked = 0
    if made > 1:
        conn.execute(
            "CREATE VECTOR INDEX wallet_habit IF NOT EXISTS FOR (w:Wallet) ON (w.habit) "
            "OPTIONS {indexConfig: {`vector.dimensions`: %d, `vector.similarity_function`: 'cosine'}}"
            % HABIT_DIM)
        cur = conn.execute(
            "MATCH (a:Wallet) WHERE a.habit IS NOT NULL "
            "CALL db.index.vector.queryNodes('wallet_habit', $k, a.habit) YIELD node, score "
            "WITH a, node, score WHERE node <> a "
            "MERGE (a)-[e:PEER_OF]->(node) SET e.score = round(score, 4) RETURN count(e) AS n",
            {"k": peers + 1})
        linked = cur.single()["n"]
    conn.commit()

    # 5. Evidence: with enough wallets, test the patterns here. Otherwise use the bundled study.
    from . import study
    wallets = conn.execute("MATCH (w:Wallet) RETURN count(w) AS n").single()["n"]
    if wallets >= min_cohort:
        study.evidence(conn)
        conn.execute("MATCH (x:Pattern) SET x.evidence_from = $s", {"s": f"this store: {wallets} wallets"})
        study.audit(conn)
        source = "tested in this store"
    else:
        source = study.apply_bundled(conn) or "none"
    conn.commit()
    return {"wallets_with_habit": made, "peer_links": linked, "evidence": source}


FEATURES = ["quick_after_loss", "size_after_loss", "flip", "chase",
            "average_down", "taker", "hold_log", "pace"]


def _habit_vectors(conn, min_positions):
    """Eight numbers that describe how a wallet behaves, z-scored across the store."""
    rows = [dict(r) for r in conn.execute(
        """
        MATCH (w:Wallet)-[:TRADED]->(p:Position)
        OPTIONAL MATCH (p)-[r:OPENED_AFTER]->(prev:Position)
        WITH w, p, r, prev,
             CASE WHEN prev IS NOT NULL AND NOT prev.won AND r.gap_min < 15 THEN 1.0 ELSE 0.0 END AS qal,
             CASE WHEN prev IS NOT NULL AND prev.won AND r.gap_min < 15 THEN 1.0 ELSE 0.0 END AS qaw,
             CASE WHEN prev IS NOT NULL AND NOT prev.won AND r.gap_min < 15 AND prev.coin = p.coin
                       AND prev.side <> p.side THEN 1.0 ELSE 0.0 END AS flip,
             CASE WHEN prev IS NOT NULL AND NOT prev.won AND r.gap_min < 15 AND prev.coin = p.coin
                       AND prev.side = p.side THEN 1.0 ELSE 0.0 END AS chase,
             CASE WHEN prev IS NOT NULL AND NOT prev.won AND r.size_ratio IS NOT NULL
                  THEN CASE WHEN r.size_ratio > 5 THEN 5.0 ELSE r.size_ratio END END AS ratio
        RETURN w.address AS wallet, count(p) AS n, avg(qal) AS quick_after_loss,
               avg(ratio) AS size_after_loss, avg(flip) AS flip, avg(chase) AS chase,
               avg(qaw) AS quick_after_win, avg(p.taker_share) AS taker,
               avg(log10(p.minutes + 1.0)) AS hold_log,
               avg(CASE WHEN p.adds_against >= 1 THEN 1.0 ELSE 0.0 END) AS average_down,
               (max(p.opened_at) - min(p.opened_at)) / 86400000.0 AS days
        """)]
    rows = [r for r in rows if r["n"] >= min_positions]
    for r in rows:
        r["size_after_loss"] = r["size_after_loss"] if r["size_after_loss"] is not None else 1.0
        r["pace"] = math.log10(1 + r["n"] / max(r["days"], 1.0))
    conn.execute("MATCH (w:Wallet) WHERE w.habit IS NOT NULL REMOVE w.habit")
    if not rows:
        return 0
    stats = {}
    for f in FEATURES:
        vals = [r[f] for r in rows]
        mean = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals)) or 1.0
        stats[f] = (mean, sd)
    payload = []
    for r in rows:
        vec = [max(-3.0, min(3.0, (r[f] - stats[f][0]) / stats[f][1])) for f in FEATURES]
        if not any(abs(v) > 1e-9 for v in vec):
            vec[0] = 1e-6  # cosine is undefined for the zero vector
        payload.append({"w": r["wallet"], "v": vec})
    for i in range(0, len(payload), 500):
        conn.execute("UNWIND $rows AS r MATCH (w:Wallet {address: r.w}) "
                     "SET w.habit = vector(r.v, %d, FLOAT32)" % HABIT_DIM, {"rows": payload[i:i + 500]})
    return len(payload)

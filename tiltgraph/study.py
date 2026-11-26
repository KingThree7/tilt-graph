"""Which habits really hurt? Measured, not assumed, and checked on data the rule never saw.

Two traps make habits look worse than they are, and both are avoided here.

  Pooling. "Positions opened right after a loss win less often" mostly says that weak traders
  lose more, so they are "right after a loss" more often. So every wallet is compared with
  itself, and the effect is the average of those differences over wallets, with a t value
  (how many standard errors from zero).

  Selection. "Positions you added to under water did worse than your others" is true of any
  position that is under water, added to or not. So adding is judged at the decision point:
  among a wallet's positions that all stood 1% to 2% against the entry with no add so far,
  the ones that were added to there, against the ones that were not. That needs the worst
  move of each position (from candles), stored as Position.worst_move_pct.

A habit counts as solid only if the difference also shows on the later half of every
wallet's history. The result is written on the Pattern nodes, so the guard and any agent
read the evidence from the same place as the data.
"""
import json
import math
import os

ROWS_Q = """
MATCH (w:Wallet)-[:TRADED]->(p:Position) WHERE w.address IN $ws
OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
RETURN p.id AS id, p.wallet AS wallet, p.opened_at AS opened_at, p.won AS won, p.ret AS ret,
       p.liquidated AS liquidated, p.pnl AS pnl, p.worst_move_pct AS worst,
       p.first_add_pct AS first_add, collect(x.name) AS patterns
"""
MIN_EACH_SIDE = 8      # entries with and without a pattern a wallet needs to be counted
MIN_AT_DEPTH = 3       # the same, for decision points under water (they are rarer)
CLIP = 0.20            # returns are cut at +-20% of position size so one blow-up cannot decide
BANDS = [(1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 10.0)]   # % under water
DECIDED_UNDER_WATER = {"average_down"}
BASIS = {
    True: "against the same wallet's positions that were equally far under water and were not added to",
    False: "against the same wallet's other positions",
}


def _mean(xs):
    return sum(xs) / len(xs)


def _t(xs):
    if len(xs) < 3:
        return 0.0
    m = _mean(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m / (sd / math.sqrt(len(xs))) if sd else 0.0


def _clip(x):
    return max(-CLIP, min(CLIP, x))


def load(conn):
    by_wallet = {}
    wallets = [r["a"] for r in conn.execute("MATCH (w:Wallet) RETURN w.address AS a")]
    for i in range(0, len(wallets), 100):  # in pieces, so a large cohort stays one small read each
        for r in conn.execute(ROWS_Q, {"ws": wallets[i:i + 100]}):
            by_wallet.setdefault(r["wallet"], []).append(dict(r))
    for rows in by_wallet.values():
        rows.sort(key=lambda r: r["opened_at"])
    return by_wallet


def at_depth(rows, low, high):
    """Positions that reached `low`% under water with no add so far: (added there, did not)."""
    there = [r for r in rows if r["worst"] is not None and r["worst"] >= low
             and (r["first_add"] is None or r["first_add"] >= low)]
    added = [r for r in there if r["first_add"] is not None and r["first_add"] < high]
    waited = [r for r in there if r["first_add"] is None or r["first_add"] >= high]
    return added, waited


def split(rows, pattern, band=BANDS[0]):
    if pattern in DECIDED_UNDER_WATER:
        return at_depth(rows, *band)
    return ([r for r in rows if pattern in r["patterns"]], [r for r in rows if pattern not in r["patterns"]])


def effect(by_wallet, pattern, part="all", band=BANDS[0]):
    """Within-wallet effect of one pattern. part: 'all', 'first' or 'second' half of each history."""
    need = MIN_AT_DEPTH if pattern in DECIDED_UNDER_WATER else MIN_EACH_SIDE
    d_win, d_ret, d_liq, n_yes, n_no = [], [], [], 0, 0
    for rows in by_wallet.values():
        half = len(rows) // 2
        rows = {"all": rows, "first": rows[:half], "second": rows[half:]}[part]
        yes, no = split(rows, pattern, band)
        if len(yes) < need or len(no) < need:
            continue
        n_yes += len(yes)
        n_no += len(no)
        d_win.append(100.0 * (_mean([r["won"] for r in yes]) - _mean([r["won"] for r in no])))
        d_ret.append(1e4 * (_mean([_clip(r["ret"]) for r in yes]) - _mean([_clip(r["ret"]) for r in no])))
        d_liq.append(1000.0 * (_mean([r["liquidated"] for r in yes]) - _mean([r["liquidated"] for r in no])))
    if len(d_win) < 3:
        return None
    return {
        "wallets": len(d_win), "positions": n_yes, "compared_with": n_no,
        "win_pts": round(_mean(d_win), 1), "win_t": round(_t(d_win), 1),
        "ret_bps": round(_mean(d_ret), 1), "ret_t": round(_t(d_ret), 1),
        "liq_per_1000": round(_mean(d_liq), 1), "liq_t": round(_t(d_liq), 1),
    }


def grade(full, later):
    if full is None or later is None:
        return "untested"
    if full["ret_t"] <= -3 and later["ret_t"] <= -2:
        return "solid: costs money, also on later data"
    if full["liq_t"] >= 3 and later["liq_per_1000"] > 0 and later["liq_t"] >= 1.5:
        return "solid: more liquidations, also on later data; average return unchanged"
    if full["win_t"] <= -2.5 and later["win_pts"] < 0:
        return "weak: fewer wins, but no measurable cost"
    return "none: no reliable difference"


def evidence(conn, write=True):
    """Test every pattern, grade the evidence, and store it on the Pattern nodes."""
    by_wallet = load(conn)
    names = [r["n"] for r in conn.execute("MATCH (x:Pattern) RETURN x.name AS n ORDER BY n")]
    total = sum(r["pnl"] for rows in by_wallet.values() for r in rows)
    n_all = sum(len(rows) for rows in by_wallet.values())
    out = []
    for name in names:
        full, later = effect(by_wallet, name), effect(by_wallet, name, "second")
        shown = [r for rows in by_wallet.values() for r in rows if name in r["patterns"]]
        row = {"pattern": name, "grade": grade(full, later), "positions": len(shown),
               "share_of_positions_pct": round(100.0 * len(shown) / n_all, 1) if n_all else 0.0,
               "share_of_net_result_pct": round(100.0 * sum(r["pnl"] for r in shown) / total, 1) if total else 0.0,
               "compared": BASIS[name in DECIDED_UNDER_WATER]}
        if full and later:
            row.update({"wallets": full["wallets"], "win_pts": full["win_pts"], "win_t": full["win_t"],
                        "ret_bps": full["ret_bps"], "ret_t": full["ret_t"],
                        "liq_per_1000": full["liq_per_1000"], "liq_t": full["liq_t"],
                        "later_ret_bps": later["ret_bps"], "later_liq_per_1000": later["liq_per_1000"]})
        out.append(row)
        if write:
            params = {f: row.get(f) for f in FIELDS}
            conn.execute("MATCH (x:Pattern {name: $name}) SET " + ", ".join(f"x.{f} = ${f}" for f in FIELDS),
                         {"name": name, **params})
    if write:
        conn.commit()
    out.sort(key=lambda r: (r["grade"] != "untested", r["grade"]), reverse=True)
    return out


def under_water(conn):
    """The decision under water, band by band: add, or do not add? Pooled and wallet by wallet."""
    by_wallet = load(conn)
    out = []
    for band in BANDS:
        added = [r for rows in by_wallet.values() for r in at_depth(rows, *band)[0]]
        waited = [r for rows in by_wallet.values() for r in at_depth(rows, *band)[1]]
        if not added or not waited:
            continue
        row = {"under_water_pct": f"{band[0]:g} to {band[1]:g}", "reached_it": len(added) + len(waited),
               "added_there": len(added)}
        for key, rows in (("added", added), ("did_not", waited)):
            row[f"{key}_win_pct"] = round(100 * _mean([r["won"] for r in rows]), 1)
            row[f"{key}_return_bps"] = round(1e4 * _mean([_clip(r["ret"]) for r in rows]), 1)
            row[f"{key}_liq_per_1000"] = round(1000 * _mean([r["liquidated"] for r in rows]), 1)
        e = effect(by_wallet, "average_down", band=band)
        if e:
            row.update({"wallets_compared": e["wallets"], "same_wallet_return_bps": e["ret_bps"],
                        "return_t": e["ret_t"], "same_wallet_liq_per_1000": e["liq_per_1000"],
                        "liq_t": e["liq_t"]})
        row["closing_there_bps"] = -round(100 * band[0])
        row["holding_on_ended_at_bps"] = round(1e4 * _mean([_clip(r["ret"]) for r in added + waited]), 1)
        out.append(row)
    return out


def audit(conn):
    """Walk forward: grade on the first half of every wallet, then let that rule speak on the second.

    Each position of the second half that falls under a solid rule gets a GUARD_SAID edge to a
    Verdict node: WAIT where the habit was followed, GO where the same situation was handled
    without it. Advice and outcome then sit side by side in the graph.
    """
    by_wallet = load(conn)
    names = [r["n"] for r in conn.execute("MATCH (x:Pattern) RETURN x.name AS n")]
    rule = []
    for name in names:
        first = effect(by_wallet, name, "first")
        if first and (first["ret_t"] <= -3 or first["liq_t"] >= 3):
            rule.append(name)
    said, per_rule = {}, {name: ([], []) for name in rule}
    for rows in by_wallet.values():
        later = rows[len(rows) // 2:]
        for name in rule:
            yes, no = split(later, name)
            per_rule[name][0].extend(yes)
            per_rule[name][1].extend(no)
            for r in no:
                said.setdefault(r["id"], ("GO", ""))
            for r in yes:
                said[r["id"]] = ("WAIT", name)
    conn.execute("MATCH (:Position)-[g:GUARD_SAID]->(:Verdict) DELETE g")
    for name in ("GO", "WAIT"):
        conn.execute("MERGE (:Verdict {name: $n})", {"n": name})
    batch = [{"id": k, "v": v[0], "why": v[1]} for k, v in said.items()]
    for i in range(0, len(batch), 2000):
        conn.execute("UNWIND $rows AS r MATCH (p:Position {id: r.id}), (v:Verdict {name: r.v}) "
                     "MERGE (p)-[g:GUARD_SAID]->(v) SET g.because = r.why", {"rows": batch[i:i + 2000]})
    result = [dict(r) for r in conn.execute(AUDIT_Q)]
    conn.execute("MATCH (v:Verdict) REMOVE v.positions")
    for r in result:  # the guard's own track record, kept where the guard can read it
        conn.execute("MATCH (v:Verdict {name: $n}) SET v.positions = $p, v.win_pct = $w, "
                     "v.avg_return_bps = $r, v.liquidated_per_1000 = $l, v.rule = $rule",
                     {"n": r["guard_said"], "p": r["positions"], "w": r["win_pct"],
                      "r": r["avg_return_bps"], "l": r["liquidated_per_1000"], "rule": ", ".join(rule)})
    conn.commit()
    by_rule = []
    for name in rule:  # the same replay, one rule at a time, each against its own comparison
        for said_, rows in (("WAIT", per_rule[name][0]), ("GO", per_rule[name][1])):
            if rows:
                by_rule.append({"rule": name, "guard_said": said_, "positions": len(rows),
                                "win_pct": round(100 * _mean([r["won"] for r in rows]), 1),
                                "avg_return_bps": round(1e4 * _mean([_clip(r["ret"]) for r in rows]), 1),
                                "liquidated_per_1000": round(1000 * _mean([r["liquidated"] for r in rows]), 1)})
    return {"rule_learned_on_first_half": rule, "result_on_second_half": result, "by_rule": by_rule}


AUDIT_Q = """
MATCH (p:Position)-[:GUARD_SAID]->(v:Verdict)
RETURN v.name AS guard_said, count(p) AS positions, count(DISTINCT p.wallet) AS wallets,
       round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
       round(10000 * avg(CASE WHEN p.ret > 0.2 THEN 0.2 WHEN p.ret < -0.2 THEN -0.2 ELSE p.ret END), 1)
         AS avg_return_bps,
       round(1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p), 1) AS liquidated_per_1000,
       round(sum(p.pnl), 0) AS total_pnl
ORDER BY guard_said
"""


BUNDLED = os.path.join(os.path.dirname(__file__), "evidence.json")
FIELDS = ["grade", "wallets", "win_pts", "win_t", "ret_bps", "ret_t", "liq_per_1000", "liq_t",
          "later_ret_bps", "later_liq_per_1000", "share_of_positions_pct", "share_of_net_result_pct",
          "compared"]


def export(conn, path=BUNDLED):
    """Save this store's evidence so that small stores, with one wallet, can still use it."""
    size = conn.execute("MATCH (w:Wallet)-[:TRADED]->(p:Position) RETURN count(DISTINCT w) AS wallets, "
                        "count(p) AS positions, min(p.opened_at) AS first, max(p.opened_at) AS last").single()
    doc = {
        "source": f"cohort study: {size['wallets']} Hyperliquid wallets, {size['positions']} closed positions",
        "wallets": size["wallets"], "positions": size["positions"],
        "first_ms": size["first"], "last_ms": size["last"],
        "patterns": {r["name"]: {f: r[f] for f in FIELDS} for r in conn.execute(
            "MATCH (x:Pattern) RETURN x.name AS name, " + ", ".join(f"x.{f} AS {f}" for f in FIELDS))},
        "track_record": [dict(r) for r in conn.execute(
            "MATCH (v:Verdict) RETURN v.name AS name, v.positions AS positions, v.win_pct AS win_pct, "
            "v.avg_return_bps AS avg_return_bps, v.liquidated_per_1000 AS liquidated_per_1000, v.rule AS rule")],
    }
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
    return doc


def apply_bundled(conn, path=BUNDLED):
    """Put the bundled cohort evidence on the Pattern nodes of a store too small to test anything."""
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        doc = json.load(fh)
    for name, vals in doc["patterns"].items():
        sets = ", ".join(f"x.{f} = ${f}" for f in FIELDS)
        conn.execute(f"MATCH (x:Pattern {{name: $name}}) SET {sets}, x.evidence_from = $src",
                     {"name": name, "src": doc["source"], **{f: vals.get(f) for f in FIELDS}})
    for v in doc.get("track_record", []):
        conn.execute("MERGE (x:Verdict {name: $name}) SET x.positions = $positions, x.win_pct = $win_pct, "
                     "x.avg_return_bps = $avg_return_bps, x.liquidated_per_1000 = $liquidated_per_1000, "
                     "x.rule = $rule, x.evidence_from = $src", {**v, "src": doc["source"]})
    conn.commit()
    return doc["source"]


AUDIT_Q = """
MATCH (p:Position)-[:GUARD_SAID]->(v:Verdict)
RETURN v.name AS guard_said, count(p) AS positions, count(DISTINCT p.wallet) AS wallets,
       round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
       round(10000 * avg(CASE WHEN p.ret > 0.2 THEN 0.2 WHEN p.ret < -0.2 THEN -0.2 ELSE p.ret END), 1)
         AS avg_return_bps,
       round(1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p), 1) AS liquidated_per_1000,
       round(sum(p.pnl), 0) AS total_pnl
ORDER BY guard_said
"""

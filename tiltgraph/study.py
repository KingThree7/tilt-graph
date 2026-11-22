"""Which habits really hurt? Measured, not assumed, and checked on data the rule never saw.

The naive way to test a habit is to pool all wallets: "positions opened right after a loss
win less often". That mostly measures something else: weak traders lose more, so they are
"right after a loss" more often. The numbers here avoid that trap:

  within wallet   each wallet's positions that show a pattern are compared with the same
                  wallet's other positions. The effect is the average of those differences
                  over wallets, with a t value (how many standard errors from zero).
  out of sample   the same test on the later half of every wallet's history only. A habit
                  counts as solid evidence only if it holds there too.

The result is written back into the graph on each Pattern node, so the guard and any agent
read the evidence from the same place as the data.
"""
import json
import math
import os

ROWS_Q = """
MATCH (p:Position)
OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
RETURN p.id AS id, p.wallet AS wallet, p.opened_at AS opened_at, p.won AS won, p.ret AS ret,
       p.liquidated AS liquidated, p.pnl AS pnl, collect(x.name) AS patterns
"""
MIN_EACH_SIDE = 8      # positions with and without the pattern a wallet needs to be counted
SOLID_T = -3.0         # t value on return that counts as solid, in sample and out of sample
CLIP = 0.20            # returns are cut at +-20% of position size so one blow-up cannot decide


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
    for r in conn.execute(ROWS_Q):
        by_wallet.setdefault(r["wallet"], []).append(dict(r))
    for rows in by_wallet.values():
        rows.sort(key=lambda r: r["opened_at"])
    return by_wallet


def effect(by_wallet, pattern, part="all"):
    """Within-wallet effect of one pattern. part: 'all', 'first' or 'second' half of each history."""
    d_win, d_ret, d_liq, flagged = [], [], [], 0
    for rows in by_wallet.values():
        half = len(rows) // 2
        rows = {"all": rows, "first": rows[:half], "second": rows[half:]}[part]
        yes = [r for r in rows if pattern in r["patterns"]]
        no = [r for r in rows if pattern not in r["patterns"]]
        if len(yes) < MIN_EACH_SIDE or len(no) < MIN_EACH_SIDE:
            continue
        flagged += len(yes)
        d_win.append(100.0 * (_mean([r["won"] for r in yes]) - _mean([r["won"] for r in no])))
        d_ret.append(1e4 * (_mean([_clip(r["ret"]) for r in yes]) - _mean([_clip(r["ret"]) for r in no])))
        d_liq.append(1000.0 * (_mean([r["liquidated"] for r in yes]) - _mean([r["liquidated"] for r in no])))
    if not d_win:
        return None
    return {
        "wallets": len(d_win), "positions": flagged,
        "win_pts": round(_mean(d_win), 1), "win_t": round(_t(d_win), 1),
        "ret_bps": round(_mean(d_ret), 1), "ret_t": round(_t(d_ret), 1),
        "liq_per_1000": round(_mean(d_liq), 1),
        "wallets_worse_pct": round(100.0 * sum(1 for d in d_ret if d < 0) / len(d_ret)),
    }


def evidence(conn, write=True):
    """Test every pattern, grade the evidence, and store it on the Pattern nodes."""
    by_wallet = load(conn)
    names = [r["n"] for r in conn.execute("MATCH (x:Pattern) RETURN x.name AS n ORDER BY n")]
    total = sum(r["pnl"] for rows in by_wallet.values() for r in rows)
    out = []
    for name in names:
        full, later = effect(by_wallet, name), effect(by_wallet, name, "second")
        share = sum(1 for rows in by_wallet.values() for r in rows if name in r["patterns"])
        pnl = sum(r["pnl"] for rows in by_wallet.values() for r in rows if name in r["patterns"])
        n_all = sum(len(rows) for rows in by_wallet.values())
        row = {"pattern": name, "positions": share,
               "share_of_positions_pct": round(100.0 * share / n_all, 1) if n_all else 0.0,
               "share_of_net_result_pct": round(100.0 * pnl / total, 1) if total else 0.0,
               "total_pnl": round(pnl, 0)}
        if full is None or later is None:
            row["grade"] = "untested"
        else:
            row.update({"wallets": full["wallets"], "win_pts": full["win_pts"], "win_t": full["win_t"],
                        "ret_bps": full["ret_bps"], "ret_t": full["ret_t"],
                        "liq_per_1000": full["liq_per_1000"],
                        "later_half_win_pts": later["win_pts"], "later_half_ret_bps": later["ret_bps"],
                        "later_half_ret_t": later["ret_t"]})
            if full["ret_t"] <= SOLID_T and later["ret_t"] <= SOLID_T:
                row["grade"] = "solid: costs money, and holds on later data"
            elif full["ret_t"] >= -SOLID_T and later["ret_t"] >= -SOLID_T:
                row["grade"] = "solid: goes with better results, and holds on later data"
            elif full["win_t"] <= SOLID_T + 1 and later["win_pts"] < 0:
                row["grade"] = "weak: fewer wins, but no measurable cost"
            else:
                row["grade"] = "none: no reliable difference"
        out.append(row)
        if write:
            conn.execute(
                "MATCH (x:Pattern {name: $n}) SET x.grade = $g, x.tested_wallets = $w, x.win_pts = $wp, "
                "x.ret_bps = $rb, x.ret_t = $rt, x.later_half_ret_bps = $lb, x.later_half_ret_t = $lt, "
                "x.share_of_positions_pct = $sp, x.share_of_net_result_pct = $sn",
                {"n": name, "g": row["grade"], "w": row.get("wallets"), "wp": row.get("win_pts"),
                 "rb": row.get("ret_bps"), "rt": row.get("ret_t"), "lb": row.get("later_half_ret_bps"),
                 "lt": row.get("later_half_ret_t"), "sp": row["share_of_positions_pct"],
                 "sn": row["share_of_net_result_pct"]})
    if write:
        conn.commit()
    out.sort(key=lambda r: r.get("ret_t", 0))
    return out


def audit(conn):
    """Walk forward: let the guard speak on the later half, using only what the first half showed.

    Evidence is graded on the first half of every wallet's history. On the second half each
    position gets the verdict that grading implies, as a GUARD_SAID edge to a Verdict node.
    Advice and outcome then sit side by side in the graph.
    """
    by_wallet = load(conn)
    names = [r["n"] for r in conn.execute("MATCH (x:Pattern) RETURN x.name AS n")]
    harmful = []
    for name in names:
        first = effect(by_wallet, name, "first")
        if first and first["ret_t"] <= SOLID_T:
            harmful.append(name)
    said = []
    for rows in by_wallet.values():
        for r in rows[len(rows) // 2:]:
            hit = [n for n in harmful if n in r["patterns"]]
            said.append({"id": r["id"], "v": "WAIT" if hit else "GO", "why": ", ".join(hit)})
    conn.execute("MATCH (:Position)-[g:GUARD_SAID]->(:Verdict) DELETE g")
    for name in ("GO", "WAIT"):
        conn.execute("MERGE (:Verdict {name: $n})", {"n": name})
    for i in range(0, len(said), 2000):
        conn.execute("UNWIND $rows AS r MATCH (p:Position {id: r.id}), (v:Verdict {name: r.v}) "
                     "MERGE (p)-[g:GUARD_SAID]->(v) SET g.because = r.why", {"rows": said[i:i + 2000]})
    result = [dict(r) for r in conn.execute(AUDIT_Q)]
    for r in result:  # the guard's own track record, kept where the guard can read it
        conn.execute("MATCH (v:Verdict {name: $n}) SET v.positions = $p, v.win_pct = $w, "
                     "v.avg_return_bps = $r, v.liquidated_per_1000 = $l, v.rule = $rule",
                     {"n": r["guard_said"], "p": r["positions"], "w": r["win_pct"],
                      "r": r["avg_return_bps"], "l": r["liquidated_per_1000"], "rule": ", ".join(harmful)})
    conn.commit()
    return {"rule_learned_on_first_half": harmful, "result_on_second_half": result}


BUNDLED = os.path.join(os.path.dirname(__file__), "evidence.json")
FIELDS = ["grade", "tested_wallets", "win_pts", "ret_bps", "ret_t", "later_half_ret_bps",
          "later_half_ret_t", "share_of_positions_pct", "share_of_net_result_pct"]


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
                     {"name": name, "src": doc["source"], **vals})
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

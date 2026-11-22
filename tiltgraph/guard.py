"""The question to ask before the next trade: is this one of the habits that cost money?

The guard never predicts a price. It walks the graph:

  1. wallet -> last closed position            what just happened, how long ago
  2. the planned trade -> Pattern              would this be revenge, a chase, a flip, sizing up,
                                               or adding to a position that is under water
  3. Pattern.grade                             what the evidence says about that habit: tested
                                               wallet by wallet, and again on later data
  4. wallet -> HAS_HABIT -> Pattern            how it went the times this wallet did it
  5. wallet -> PEER_OF -> HAS_HABIT            and for the traders who behave most like it
  6. last position -> TILTED_FROM chain        am I already inside a loss spiral
  7. Verdict nodes                             how the guard's own advice did on data it had
                                               never seen

WAIT is only said for a habit graded "solid: costs money". A habit with weak evidence gets a
note, not a verdict. Every answer carries the Cypher behind it, so the claim can be checked.
"""
import datetime as dt

from .graph import state_name

CHAIN_Q = """
MATCH path = (last:Position {id: $id})-[:TILTED_FROM]->*(first:Position)
RETURN [n IN nodes(path) | {id: n.id, coin: n.coin, side: n.side, pnl: n.pnl}] AS chain
ORDER BY length(path) DESC LIMIT 1
"""
HABIT_Q = """
MATCH (x:Pattern {name: $p})
OPTIONAL MATCH (w:Wallet {address: $w})-[h:HAS_HABIT]->(x)
RETURN x.meaning AS meaning, x.grade AS grade, x.tested_wallets AS tested_wallets,
       x.win_pts AS win_pts, x.ret_bps AS ret_bps, x.later_half_ret_bps AS later_half_ret_bps,
       x.share_of_net_result_pct AS share_of_net_result_pct, x.evidence_from AS evidence_from,
       h.positions AS n, h.win_pct AS win_pct, h.pnl AS pnl, h.share AS share
"""
PEER_HABIT_Q = """
MATCH (:Wallet {address: $w})-[:PEER_OF]->(peer:Wallet)
OPTIONAL MATCH (peer)-[h:HAS_HABIT]->(:Pattern {name: $p})
RETURN count(peer) AS peers, sum(coalesce(h.positions, 0)) AS positions,
       round(sum(coalesce(h.pnl, 0.0)), 2) AS pnl,
       sum(CASE WHEN h.pnl < 0 THEN 1 ELSE 0 END) AS peers_who_lost_on_it
"""
TRACK_Q = """
MATCH (v:Verdict) WHERE v.positions IS NOT NULL
RETURN v.name AS said, v.positions AS positions, v.win_pct AS win_pct,
       v.avg_return_bps AS avg_return_bps, v.liquidated_per_1000 AS liquidated_per_1000
ORDER BY v.name
"""


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _wallet(conn, wallet):
    if wallet:
        return wallet
    rows = conn.execute("MATCH (w:Wallet) RETURN w.address AS a LIMIT 2").fetchall()
    if len(rows) == 1:
        return rows[0]["a"]
    raise ValueError("this store holds several wallets: say which one")


def check(conn, now_ms, wallet=None, coin=None, side=None, notional=None,
          adding_to_loser=False, min_own=8):
    """adding_to_loser: the planned trade adds to an open position that is under water."""
    wallet = _wallet(conn, wallet)
    last = conn.execute(
        "MATCH (:Wallet {address: $w})-[:TRADED]->(p:Position) WHERE p.closed_at <= $now "
        "RETURN p.id AS id, p.coin AS coin, p.side AS side, p.pnl AS pnl, p.won AS won, "
        "p.closed_at AS closed_at, p.max_notional AS max_notional "
        "ORDER BY p.closed_at DESC LIMIT 1", {"w": wallet, "now": now_ms}).fetchone()
    out = {"wallet": wallet, "now": _iso(now_ms)}
    gap, state = None, "calm"
    if last is not None:
        gap = (now_ms - last["closed_at"]) / 60000
        state = state_name(last["won"], gap)
        out["last_closed"] = {"id": last["id"], "coin": last["coin"], "side": last["side"],
                              "pnl": round(last["pnl"], 2), "minutes_ago": round(gap, 1)}
    out["state"] = state

    # Which habits would the planned trade be?
    flags = []
    if adding_to_loser:
        flags.append("average_down")
    if last is not None and not last["won"]:
        if gap < 15:
            flags.append("revenge")
            if coin and side and coin == last["coin"]:
                flags.append("chase" if side == last["side"] else "flip")
        if notional and last["max_notional"] and notional / last["max_notional"] >= 1.5:
            flags.append("size_up")

    matches, solid, untested = [], [], []
    for name in flags:
        h = conn.execute(HABIT_Q, {"w": wallet, "p": name}).fetchone()
        if h is None:
            continue
        peer = conn.execute(PEER_HABIT_Q, {"w": wallet, "p": name}).fetchone()
        grade = h["grade"] or "untested"
        entry = {
            "pattern": name, "meaning": h["meaning"],
            "evidence": {"grade": grade, "tested_on_wallets": h["tested_wallets"],
                         "win_rate_change_pts": h["win_pts"], "return_change_bps": h["ret_bps"],
                         "return_change_bps_on_later_data": h["later_half_ret_bps"],
                         "share_of_all_net_losses_pct": h["share_of_net_result_pct"],
                         "from": h["evidence_from"] or "this store"},
            "you": {"times": h["n"] or 0, "win_pct": h["win_pct"], "total_pnl": h["pnl"]},
        }
        if peer and peer["peers"]:
            entry["your_peers"] = {"peers": peer["peers"], "times": peer["positions"],
                                   "total_pnl": peer["pnl"],
                                   "peers_who_lost_on_it": peer["peers_who_lost_on_it"]}
        matches.append(entry)
        if grade.startswith("solid: costs"):
            solid.append(entry)
        elif grade == "untested":
            untested.append(entry)
    if matches:
        out["planned_trade_matches"] = matches

    # Am I already inside a spiral?
    if last is not None and not last["won"] and gap < 30:
        row = conn.execute(CHAIN_Q, {"id": last["id"]}).fetchone()
        chain = list(reversed(row["chain"])) if row else []
        if len(chain) > 1:
            out["spiral"] = {"losses_in_a_row": len(chain),
                             "pnl": round(sum(c["pnl"] for c in chain), 2),
                             "path": [f"{c['coin']} {c['side']} {c['pnl']:+.2f}" for c in chain]}

    if solid:
        e = solid[0]
        ev, you = e["evidence"], e["you"]
        reason = (f"This trade would be '{e['pattern']}': {e['meaning']} Across {ev['tested_on_wallets']} "
                  f"wallets, each compared with itself, such positions returned {abs(ev['return_change_bps'])} "
                  f"basis points less than the same trader's other positions, and "
                  f"{abs(ev['return_change_bps_on_later_data'])} less on later data the rule had not seen. "
                  f"They carry {ev['share_of_all_net_losses_pct']}% of all net losses.")
        if you["times"] >= min_own:
            reason += f" You did this {you['times']} times: total result {you['total_pnl']:+.2f}."
        out.update(verdict="WAIT", basis=ev["from"], reason=reason)
    elif untested and len(untested) == len(matches):
        out.update(verdict="UNKNOWN", basis="none",
                   reason="The planned trade matches a habit, but this store holds no tested evidence "
                          "about it. Run `tiltgraph evidence` on a cohort store, or keep the bundled one.")
    elif last is None and not matches:
        out.update(verdict="UNKNOWN", basis="none",
                   reason="The store holds no closed position of this wallet before this moment.")
    else:
        notes = [f"'{m['pattern']}' ({m['evidence']['grade']})" for m in matches]
        reason = ("None of the habits with solid evidence applies."
                  + (" It does match " + ", ".join(notes) + "." if notes else ""))
        if "spiral" in out:
            reason += (f" You are {out['spiral']['losses_in_a_row']} losses deep in a row; the evidence "
                       "says that lowers the win rate a little, not the result.")
        out.update(verdict="GO", basis="evidence in this store", reason=reason)

    track = [dict(r) for r in conn.execute(TRACK_Q)]
    if track:
        out["guard_track_record"] = {
            "what": "verdicts replayed on the later half of every wallet, rule learned on the first half",
            "rows": track}
    out["evidence_cypher"] = {"query": HABIT_Q.strip(), "params": {"w": wallet, "p": flags[0] if flags else None}}
    return out



"""The question to ask before the next trade: what state am I in, and how did that go before?

The guard never predicts a price. It walks the graph:

  1. wallet -> last closed position            what just happened, how long ago
  2. that gives a State                        e.g. "after a loss, under 5 min"
  3. wallet -> positions opened in that State  how did it go for me in this state
  4. wallet -> peers -> their positions there  and for traders who behave like me
  5. last position -> OPENED_AFTER chain       am I already inside a loss spiral
  6. wallet -> HAS_HABIT -> Pattern            is the planned trade one of my costly habits

Every answer names the positions it rests on and carries the Cypher that produced it,
so an agent, or a person, can check the claim instead of trusting it.
"""
import datetime as dt
import math

from .graph import STATES, state_name

OWN_Q = """
MATCH (w:Wallet {address: $w})-[:TRADED]->(p:Position)-[:OPENED_IN]->(s:State)
WHERE p.opened_at < $now
RETURN s.name AS state, count(p) AS n, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins,
       avg(p.ret) AS ret, sum(p.pnl) AS pnl
"""
PEER_Q = """
MATCH (w:Wallet {address: $w})-[:PEER_OF]->(peer:Wallet)-[:TRADED]->(p:Position)-[:OPENED_IN]->(s:State)
RETURN s.name AS state, count(p) AS n, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins,
       avg(p.ret) AS ret, count(DISTINCT peer) AS peers
"""
CHAIN_Q = """
MATCH path = (last:Position {id: $id})-[:TILTED_FROM]->*(first:Position)
RETURN [n IN nodes(path) | {id: n.id, coin: n.coin, side: n.side, pnl: n.pnl}] AS chain
ORDER BY length(path) DESC LIMIT 1
"""
RECENT_Q = """
MATCH (w:Wallet {address: $w})-[:TRADED]->(p:Position)-[:OPENED_IN]->(:State {name: $s})
WHERE p.opened_at < $now
RETURN p.id AS id, p.coin AS coin, p.side AS side, p.pnl AS pnl, p.opened_at AS opened_at
ORDER BY p.opened_at DESC LIMIT 3
"""
HABIT_Q = """
MATCH (w:Wallet {address: $w})-[h:HAS_HABIT]->(x:Pattern {name: $p})
RETURN h.positions AS n, h.win_pct AS win_pct, h.pnl AS pnl, x.meaning AS meaning
"""


def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(wins, n):
    return round(100.0 * wins / n, 1) if n else None


def _z(w1, n1, w2, n2):
    """Two-proportion z score: how far apart are two win rates, in standard errors."""
    if not n1 or not n2:
        return 0.0
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (w1 / n1 - w2 / n2) / se if se else 0.0


def _wallet(conn, wallet):
    if wallet:
        return wallet
    rows = conn.execute("MATCH (w:Wallet) RETURN w.address AS a LIMIT 2").fetchall()
    if len(rows) == 1:
        return rows[0]["a"]
    raise ValueError("this store holds several wallets: say which one")


def check(conn, now_ms, wallet=None, coin=None, side=None, notional=None,
          min_own=8, min_peer=40, gap_pts=8.0):
    wallet = _wallet(conn, wallet)
    last = conn.execute(
        "MATCH (:Wallet {address: $w})-[:TRADED]->(p:Position) WHERE p.closed_at <= $now "
        "RETURN p.id AS id, p.coin AS coin, p.side AS side, p.pnl AS pnl, p.won AS won, "
        "p.closed_at AS closed_at, p.max_notional AS max_notional "
        "ORDER BY p.closed_at DESC LIMIT 1", {"w": wallet, "now": now_ms}).fetchone()
    if last is None:
        return {"wallet": wallet, "verdict": "UNKNOWN", "basis": "none",
                "reason": "The store holds no closed position of this wallet before this moment."}
    gap = (now_ms - last["closed_at"]) / 60000
    state = state_name(last["won"], gap)

    own = {r["state"]: dict(r) for r in conn.execute(OWN_Q, {"w": wallet, "now": now_ms})}
    peer = {r["state"]: dict(r) for r in conn.execute(PEER_Q, {"w": wallet})}
    blank = {"n": 0, "wins": 0, "ret": None}

    def side_view(table):
        here, calm = table.get(state, blank), table.get("calm", blank)
        return {"positions": here["n"], "win_pct": _pct(here["wins"], here["n"]),
                "calm_positions": calm["n"], "calm_win_pct": _pct(calm["wins"], calm["n"]),
                "z": round(_z(here["wins"], here["n"], calm["wins"], calm["n"]), 2)}

    mine, theirs = side_view(own), side_view(peer)
    theirs["peers"] = max((r.get("peers", 0) for r in peer.values()), default=0)

    out = {
        "wallet": wallet,
        "now": _iso(now_ms),
        "last_closed": {"id": last["id"], "coin": last["coin"], "side": last["side"],
                        "pnl": round(last["pnl"], 2), "minutes_ago": round(gap, 1)},
        "state": state,
        "you": mine,
        "peers": theirs,
    }

    # Am I already inside a spiral?
    if not last["won"]:
        row = conn.execute(CHAIN_Q, {"id": last["id"]}).fetchone()
        chain = list(reversed(row["chain"])) if row else []
        if len(chain) > 1:
            out["spiral"] = {"losses_in_a_row": len(chain),
                             "pnl": round(sum(c["pnl"] for c in chain), 2),
                             "path": [f"{c['coin']} {c['side']} {c['pnl']:+.2f}" for c in chain]}

    # Is the planned trade one of this wallet's habits?
    flags = []
    if not last["won"] and gap < 15:
        flags.append("revenge")
        if coin and side and coin == last["coin"]:
            flags.append("chase" if side == last["side"] else "flip")
    if not last["won"] and notional and last["max_notional"]:
        if notional / last["max_notional"] >= 1.5:
            flags.append("size_up")
    habits = []
    for name in flags:
        h = conn.execute(HABIT_Q, {"w": wallet, "p": name}).fetchone()
        habits.append({"pattern": name, "times_before": h["n"] if h else 0,
                       "win_pct": h["win_pct"] if h else None,
                       "total_pnl": h["pnl"] if h else None})
    if habits:
        out["planned_trade_matches"] = habits

    # Verdict: own history first, peers when own history is too thin, otherwise say so.
    if state == "calm":
        out.update(verdict="GO", basis="state",
                   reason="Nothing closed in the last hour. This is the calm state everything else is compared with.")
    else:
        basis = None
        if mine["positions"] >= min_own and mine["calm_positions"] >= min_own:
            basis, view = "your own history", mine
        elif theirs["positions"] >= min_peer and theirs["calm_positions"] >= min_peer:
            basis, view = f"{theirs['peers']} traders with habits like yours", theirs
        if basis is None:
            out.update(verdict="UNKNOWN", basis="none",
                       reason=f"Only {mine['positions']} earlier positions of yours and {theirs['positions']} "
                              f"of your peers were opened in the state '{state}'. Too few to judge.")
        else:
            diff = view["win_pct"] - view["calm_win_pct"]
            worse = diff <= -gap_pts and view["z"] <= -1.28
            costly = [h for h in habits if h["times_before"] >= min_own and (h["total_pnl"] or 0) < 0]
            verdict = "WAIT" if worse or costly else "GO"
            reason = (f"Based on {basis}: {view['positions']} positions opened in the state '{state}' "
                      f"won {view['win_pct']}% of the time, against {view['calm_win_pct']}% when calm.")
            if not worse and diff < 0:
                reason += " The gap is too small to tell apart from chance."
            if costly:
                reason += " The planned trade is also a habit that has cost you money: " + ", ".join(
                    f"{h['pattern']} ({h['times_before']}x, {h['total_pnl']:+.2f})" for h in costly) + "."
            out.update(verdict=verdict, basis=basis, reason=reason)
            if verdict == "WAIT":
                out["wait_minutes"] = _wait(own if basis.startswith("your") else peer, last["won"], gap,
                                            min_own if basis.startswith("your") else min_peer)

    recent = conn.execute(RECENT_Q, {"w": wallet, "s": state, "now": now_ms}).fetchall()
    out["evidence"] = {
        "last_times_in_this_state": [
            {"id": r["id"], "trade": f"{r['coin']} {r['side']}", "pnl": round(r["pnl"], 2),
             "opened": _iso(r["opened_at"])} for r in recent],
        "cypher": OWN_Q.strip(),
        "params": {"w": wallet, "now": now_ms},
    }
    return out


def _wait(table, last_won, gap, min_n, tolerance=5.0):
    """Minutes until the first later state whose win rate is back near the calm one."""
    calm = table.get("calm")
    if not calm or not calm["n"]:
        return max(0, round(60 - gap))
    calm_pct = 100.0 * calm["wins"] / calm["n"]
    for name, after, low, high in STATES:
        if after != ("win" if last_won else "loss") or low <= gap:
            continue
        row = table.get(name)
        if row and row["n"] >= min_n and 100.0 * row["wins"] / row["n"] >= calm_pct - tolerance:
            return max(0, round(low - gap))
    return max(0, round(60 - gap))

"""Questions the graph can answer and a table cannot. Each one is a Cypher statement."""

QUESTIONS = {
    "overall": (
        "How did all closed positions do?",
        """
        MATCH (p:Position)
        RETURN count(p) AS positions, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins,
               round(sum(p.pnl), 2) AS pnl, round(sum(p.fees), 2) AS fees
        """),
    "patterns": (
        "What does each pattern cost compared with calm trades?",
        """
        MATCH (p:Position)
        OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
        WITH p, coalesce(x.name, 'calm (no pattern)') AS pattern
        RETURN pattern, count(p) AS positions,
               round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
               round(avg(p.pnl), 2) AS avg_pnl, round(sum(p.pnl), 2) AS total_pnl
        ORDER BY total_pnl
        """),
    "after_loss_by_gap": (
        "Does waiting after a loss help? Result of the next position by waiting time.",
        """
        MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position)
        WHERE prev.won = false
        WITH p, CASE WHEN r.gap_min <= 5 THEN '1. within 5 min'
                     WHEN r.gap_min <= 15 THEN '2. 5 to 15 min'
                     WHEN r.gap_min <= 60 THEN '3. 15 to 60 min'
                     ELSE '4. over 1 hour' END AS waited
        RETURN waited, count(p) AS positions,
               round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
               round(avg(p.pnl), 2) AS avg_pnl, round(sum(p.pnl), 2) AS total_pnl
        ORDER BY waited
        """),
    "after_win_vs_loss": (
        "Is the next position different after a win than after a loss?",
        """
        MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position)
        RETURN CASE WHEN prev.won THEN 'after a win' ELSE 'after a loss' END AS previous,
               count(p) AS positions,
               round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
               round(avg(r.size_ratio), 2) AS avg_size_vs_previous,
               round(avg(r.gap_min), 1) AS avg_wait_min, round(sum(p.pnl), 2) AS total_pnl
        ORDER BY previous
        """),
    "spirals": (
        "The worst spirals: chains of positions each opened within 30 minutes after a loss.",
        """
        MATCH (last:Position)
        WHERE EXISTS { (last)-[:TILTED_FROM]->(:Position) }
          AND NOT EXISTS { (:Position)-[:TILTED_FROM]->(last) }
        MATCH path = (last)-[:TILTED_FROM]->+(first:Position)
        WHERE NOT EXISTS { (first)-[:TILTED_FROM]->(:Position) }
        WITH nodes(path) AS ns
        RETURN ns[0].wallet AS wallet, size(ns) AS positions,
               round(reduce(s = 0.0, n IN ns | s + n.pnl), 2) AS pnl,
               [n IN reverse(ns) | n.coin + ' ' + n.side] AS sequence,
               toString(datetime({epochMillis: ns[-1].opened_at})) AS started
        ORDER BY pnl LIMIT 5
        """),
    "coins": (
        "Which coins pull this trader into patterns?",
        """
        MATCH (p:Position)-[:ON]->(c:Coin)
        OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
        WITH c, p, count(x) AS flags
        RETURN c.name AS coin, count(p) AS positions,
               sum(CASE WHEN flags > 0 THEN 1 ELSE 0 END) AS in_a_pattern,
               round(sum(p.pnl), 2) AS total_pnl
        ORDER BY in_a_pattern DESC, positions DESC LIMIT 8
        """),
    "hours": (
        "At which hours (UTC) do the patterns happen?",
        """
        MATCH (p:Position)-[:SHOWS]->(:Pattern)
        WITH DISTINCT p
        RETURN p.hour AS hour_utc, count(p) AS pattern_positions, round(sum(p.pnl), 2) AS total_pnl
        ORDER BY pattern_positions DESC LIMIT 6
        """),
}


# Questions over many wallets at once. They only make sense on a cohort store.
STUDY = {
    "size": (
        "How much data is this?",
        """
        MATCH (w:Wallet)-[:TRADED]->(p:Position)
        RETURN count(DISTINCT w) AS wallets, count(p) AS positions,
               sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) AS liquidations,
               toString(date(datetime({epochMillis: min(p.opened_at)}))) AS first_day,
               toString(date(datetime({epochMillis: max(p.opened_at)}))) AS last_day
        """),
    "states": (
        "How do positions do, by the state they were opened in?",
        """
        MATCH (p:Position)-[:OPENED_IN]->(s:State)
        WITH s, count(p) AS positions, count(DISTINCT p.wallet) AS wallets,
             100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p) AS win,
             1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p) AS liq
        RETURN s.name AS state, wallets, positions, round(win, 1) AS win_pct,
               round(liq, 1) AS liquidated_per_1000
        ORDER BY s.rank
        """),
    "within_wallet": (
        "Is it the same trader doing worse, or just worse traders trading more? "
        "Each wallet compared with itself: within 15 minutes after a loss against calm.",
        """
        MATCH (w:Wallet)-[:TRADED]->(p:Position)-[:OPENED_IN]->(s:State)
        WITH w,
             sum(CASE WHEN s.after = 'loss' AND s.high_min <= 15 THEN 1 ELSE 0 END) AS hot,
             sum(CASE WHEN s.after = 'loss' AND s.high_min <= 15 AND p.won THEN 1 ELSE 0 END) AS hot_wins,
             sum(CASE WHEN s.name = 'calm' THEN 1 ELSE 0 END) AS calm,
             sum(CASE WHEN s.name = 'calm' AND p.won THEN 1 ELSE 0 END) AS calm_wins
        WHERE hot >= 10 AND calm >= 10
        WITH 100.0 * hot_wins / hot - 100.0 * calm_wins / calm AS diff
        RETURN count(*) AS wallets_with_enough_data,
               sum(CASE WHEN diff < 0 THEN 1 ELSE 0 END) AS worse_after_a_loss,
               sum(CASE WHEN diff <= -10 THEN 1 ELSE 0 END) AS worse_by_10_points_or_more,
               round(avg(diff), 1) AS average_change_in_win_pct,
               round(percentileCont(diff, 0.5), 1) AS median_change_in_win_pct
        """),
    "patterns": (
        "What does each pattern do across all wallets?",
        """
        MATCH (p:Position)
        OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
        WITH p, coalesce(x.name, 'no pattern') AS pattern
        RETURN pattern, count(p) AS positions, count(DISTINCT p.wallet) AS wallets,
               round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
               round(1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p), 1) AS liquidated_per_1000,
               round(sum(p.pnl), 0) AS total_pnl
        ORDER BY win_pct
        """),
    "spiral_depth": (
        "How deep do loss spirals go, and how do they end?",
        """
        MATCH (last:Position)
        WHERE EXISTS { (last)-[:TILTED_FROM]->(:Position) }
          AND NOT EXISTS { (:Position)-[:TILTED_FROM]->(last) }
        MATCH path = (last)-[:TILTED_FROM]->+(first:Position)
        WHERE NOT EXISTS { (first)-[:TILTED_FROM]->(:Position) }
        WITH size(nodes(path)) AS depth, last, reduce(s = 0.0, n IN nodes(path) | s + n.pnl) AS pnl
        RETURN depth, count(*) AS spirals, count(DISTINCT last.wallet) AS wallets,
               round(100.0 * sum(CASE WHEN last.won THEN 1 ELSE 0 END) / count(*), 1) AS last_trade_won_pct,
               sum(CASE WHEN last.liquidated THEN 1 ELSE 0 END) AS ended_in_liquidation,
               round(avg(pnl), 2) AS avg_pnl
        ORDER BY depth
        """),
    "sizing": (
        "Do traders size up after a loss?",
        """
        MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position)
        WHERE r.gap_min < 60 AND r.size_ratio IS NOT NULL
        RETURN CASE WHEN prev.won THEN 'after a win' ELSE 'after a loss' END AS previous,
               count(p) AS positions,
               round(percentileCont(r.size_ratio, 0.5), 2) AS median_size_vs_previous,
               round(100.0 * sum(CASE WHEN r.size_ratio >= 1.5 THEN 1 ELSE 0 END) / count(p), 1) AS pct_sized_up_1_5x
        ORDER BY previous
        """),
    "habit_peers": (
        "Do wallets that look alike also lose alike? Each wallet's hot-state win rate "
        "against the same number for its PEER_OF neighbours.",
        """
        MATCH (w:Wallet)-[:HAS_HABIT]->(:Pattern {name: 'revenge'})
        MATCH (w)-[:PEER_OF]->(peer:Wallet)-[h:HAS_HABIT]->(:Pattern {name: 'revenge'})
        WITH w, avg(h.share) AS peer_share, count(peer) AS peers
        MATCH (w)-[mine:HAS_HABIT]->(:Pattern {name: 'revenge'})
        RETURN CASE WHEN mine.share >= 0.2 THEN 'revenge is 20%+ of my positions'
                    ELSE 'revenge is under 20% of my positions' END AS wallet_group,
               count(w) AS wallets, round(100 * avg(mine.share), 1) AS my_revenge_share_pct,
               round(100 * avg(peer_share), 1) AS my_peers_revenge_share_pct
        ORDER BY wallet_group
        """),
}
ALL = {**QUESTIONS, **{"study_" + k: v for k, v in STUDY.items()}}


def run(conn, name):
    title, query = ALL[name]
    cur = conn.execute(query)
    rows = [dict(zip(r.keys(), r.values())) for r in cur]
    return title, query, rows


def table(rows):
    if not rows:
        return "_no rows_\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out) + "\n"


def markdown(conn, address, with_queries=False):
    parts = [f"# Tilt Graph report\n\nWallet `{address}`\n"]
    for name in QUESTIONS:
        title, query, rows = run(conn, name)
        parts.append(f"## {title}\n")
        parts.append(table(rows))
        if with_queries:
            parts.append("```cypher" + query.rstrip() + "\n```\n")
    return "\n".join(parts)

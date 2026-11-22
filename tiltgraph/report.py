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
        MATCH path = (last:Position)-[:OPENED_AFTER]->+(first:Position)
        WHERE all(r IN relationships(path) WHERE r.gap_min <= 30)
          AND all(n IN nodes(path)[1..] WHERE n.won = false)
          AND NOT EXISTS { MATCH (later:Position)-[r2:OPENED_AFTER]->(last)
                           WHERE r2.gap_min <= 30 AND last.won = false }
          AND NOT EXISTS { MATCH (first)-[r3:OPENED_AFTER]->(before:Position)
                           WHERE r3.gap_min <= 30 AND before.won = false }
        WITH path, nodes(path) AS ns
        RETURN size(ns) AS positions,
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


def run(conn, name):
    title, query = QUESTIONS[name]
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

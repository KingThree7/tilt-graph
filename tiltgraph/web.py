"""The same graph in the browser: export a store as JSON and write a page that loads it into
Relate compiled to WebAssembly. Nothing is sent anywhere; every query runs in the tab.

Meant for small stores (the made-up trader). The cohort stays a file.
"""
import json
import os

from . import report

PAGE = os.path.join(os.path.dirname(__file__), "web_page.html")

# Shown as buttons on the page. Each is plain Cypher that also runs against the .rl file.
EXTRA = {
    "longest_spiral": (
        "The longest chain of losses, as one path.",
        """
        MATCH path = (last:Position)-[:TILTED_FROM]->+(first:Position)
        WHERE NOT EXISTS { (:Position)-[:TILTED_FROM]->(last) }
          AND NOT EXISTS { (first)-[:TILTED_FROM]->(:Position) }
        RETURN length(path) + 1 AS losses_in_a_row,
               round(reduce(s = 0.0, n IN nodes(path) | s + n.pnl), 2) AS pnl,
               [n IN reverse(nodes(path)) | n.coin + ' ' + n.side + ' ' + toString(round(n.pnl, 2))] AS path
        ORDER BY losses_in_a_row DESC, pnl LIMIT 5
        """),
    "deepest_ladder": (
        "The deepest ladders of adds under water, and how they ended.",
        """
        MATCH (p:Position)-[:AVERAGED_DOWN]->(first:Add)
        MATCH path = (first)-[:THEN]->*(last:Add)
        WHERE NOT EXISTS { (last)-[:THEN]->(:Add) }
        RETURN p.coin AS coin, p.side AS side, length(path) + 1 AS adds_under_water,
               last.pct_from_entry AS last_add_pct_from_entry, round(p.pnl, 2) AS pnl,
               p.liquidated AS liquidated
        ORDER BY adds_under_water DESC, pnl LIMIT 8
        """),
    "similar_moments": (
        "Vector search: the 20 moments most like 'right after a loss, same coin, same side, "
        "three losses deep', and how they ended.",
        """
        MATCH (p:Position)
        SEARCH p IN (VECTOR INDEX position_moment
                     FOR vector([-1.0, 0.8, 0.0, 1.0, 1.0, 0.6, -0.2], 7, FLOAT32) LIMIT 20)
        SCORE AS score
        OPTIONAL MATCH (p)-[:SHOWS]->(x:Pattern)
        WITH p, score, collect(x.name) AS habits
        RETURN round(score, 3) AS similarity, p.coin AS coin, p.side AS side,
               round(p.pnl, 2) AS pnl, p.won AS won, p.liquidated AS liquidated, habits
        ORDER BY similarity DESC
        """),
    "find_question": (
        "Full-text search over the prepared questions that are stored in the graph.",
        """
        MATCH (q:Question)
        SEARCH q IN (FULLTEXT INDEX question_text FOR 'after a loss' LIMIT 5) SCORE AS score
        RETURN q.name AS name, q.title AS title, round(score, 2) AS score
        """),
    "evidence": (
        "What the cohort test says about each habit (stored on the Pattern nodes).",
        """
        MATCH (x:Pattern)
        OPTIONAL MATCH (:Wallet)-[h:HAS_HABIT]->(x)
        RETURN x.name AS habit, x.grade AS grade, x.wallets AS wallets_tested,
               x.ret_bps AS return_change_bps, x.liq_per_1000 AS liquidations_per_1000,
               h.positions AS this_trader_did_it, h.pnl AS this_trader_result
        ORDER BY x.liq_per_1000 DESC
        """),
}

VECTORS = {"Position": ("moment", 7), "Wallet": ("habit", 8)}
INDEXES = [
    "CREATE VECTOR INDEX position_moment IF NOT EXISTS FOR (p:Position) ON (p.moment) "
    "WITH [p.wallet, p.closed_at] OPTIONS {indexConfig: {`vector.dimensions`: 7, "
    "`vector.similarity_function`: 'euclidean'}}",
    "CREATE FULLTEXT INDEX question_text IF NOT EXISTS FOR (q:Question) ON EACH [q.title]",
]


def export(conn):
    """Every node and relationship of the store as plain JSON."""
    nodes, rels = {}, {}
    for r in conn.execute("MATCH (n) RETURN id(n) AS k, labels(n)[0] AS label, properties(n) AS props"):
        props = {}
        for key, value in r["props"].items():
            props[key] = list(value.values) if hasattr(value, "values") and not isinstance(value, dict) else value
        props["_k"] = r["k"]
        nodes.setdefault(r["label"], []).append(props)
    for r in conn.execute("MATCH (a)-[e]->(b) RETURN type(e) AS t, labels(a)[0] AS la, id(a) AS a, "
                          "labels(b)[0] AS lb, id(b) AS b, properties(e) AS props"):
        rels.setdefault(f"{r['t']}|{r['la']}|{r['lb']}", []).append([r["a"], r["b"], r["props"]])
    return {"nodes": nodes, "rels": rels, "vectors": VECTORS, "indexes": INDEXES}


def write(conn, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    data = export(conn)
    questions = {**EXTRA, **{k: v for k, v in report.QUESTIONS.items()}}
    data["questions"] = [{"name": k, "title": v[0], "cypher": _dedent(v[1])} for k, v in questions.items()]
    with open(os.path.join(out_dir, "demo-graph.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(PAGE) as src, open(os.path.join(out_dir, "try.html"), "w") as dst:
        dst.write(src.read())
    return {"nodes": sum(len(v) for v in data["nodes"].values()),
            "relationships": sum(len(v) for v in data["rels"].values()),
            "questions": len(data["questions"]), "page": os.path.join(out_dir, "try.html")}


def _dedent(text):
    lines = [l for l in text.strip("\n").splitlines()]
    pad = min(len(l) - len(l.lstrip()) for l in lines if l.strip())
    return "\n".join(l[pad:] for l in lines)

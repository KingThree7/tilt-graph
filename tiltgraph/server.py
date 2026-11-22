"""Tilt Graph as an MCP server: the graph as memory an agent consults before it acts.

    tiltgraph serve -d examples/cohort.rl

The store is opened read-only for every call, so an agent can look but never change it.
Each tool answers with the facts and with the Cypher behind them.
"""
import json
import time

from . import graph, guard, report

INSTRUCTIONS = """Tilt Graph holds closed trading positions as a graph and links each position to
the one that had just closed before it. Before you place or recommend a trade for a wallet,
call pre_trade_check. If the verdict is WAIT, say so, give the reason and the numbers, and do
not place the trade unless the person insists. If it is UNKNOWN, say that the history is too
thin; never turn UNKNOWN into GO. The graph describes past behaviour. It predicts no price."""

SCHEMA_TEXT = """Nodes
  (:Wallet {address, positions, win_pct, pnl, liquidations, habit: VECTOR})
  (:Position {id, wallet, coin, side, opened_at, closed_at, minutes, pnl, ret, won,
              max_notional, fills, adds, taker_share, liquidated, hour})
  (:Coin {name})  (:Day {date})
  (:State {name, after, low_min, high_min, rank})   the situation a position was opened in
  (:Pattern {name, meaning})                        revenge, size_up, flip, chase
Relationships
  (:Wallet)-[:TRADED]->(:Position)-[:ON]->(:Coin)
  (:Position)-[:ON_DAY]->(:Day)
  (:Position)-[:NEXT]->(:Position)                         next by open time
  (:Position)-[:OPENED_AFTER {gap_min, size_ratio}]->(:Position)   what had just closed
  (:Position)-[:TILTED_FROM {gap_min}]->(:Position)        opened within 30 min after that loss
  (:Position)-[:OPENED_IN]->(:State)
  (:Position)-[:SHOWS]->(:Pattern)
  (:Wallet)-[:HAS_HABIT {positions, share, win_pct, avg_ret, pnl}]->(:Pattern)
  (:Wallet)-[:PEER_OF {score}]->(:Wallet)                  nearest by habit vector
Times are milliseconds since epoch, UTC. pnl is in USDC after fees. ret is pnl / max_notional."""


def _rows(conn, query, params=None):
    return [json.loads(json.dumps(dict(r), default=str)) for r in conn.execute(query, params or {})]


def build(database):
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp 2.x
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
    app = Server("tilt-graph", instructions=INSTRUCTIONS)

    def with_store(fn):
        conn = graph.open_store(database, read_only=True)
        try:
            return fn(conn)
        finally:
            conn.close()

    @app.tool()
    def pre_trade_check(wallet: str = "", coin: str = "", side: str = "", notional: float = 0.0,
                        at_ms: int = 0) -> dict:
        """Call this before every trade. Says GO, WAIT or UNKNOWN for a wallet at this moment
        (or at at_ms), from the wallet's own history, or from its peers when that is too thin.
        coin, side ("long"/"short") and notional (USDC) describe the planned trade and are optional."""
        now = at_ms or int(time.time() * 1000)
        return with_store(lambda c: guard.check(c, now, wallet or None, coin or None, side or None,
                                                notional or None))

    @app.tool()
    def habits(wallet: str = "") -> dict:
        """What this wallet does after a loss, and what it has cost: its patterns and its
        win rate in every state."""
        def run(c):
            w = guard._wallet(c, wallet or None)
            return {
                "wallet": w,
                "patterns": _rows(c, "MATCH (:Wallet {address: $w})-[h:HAS_HABIT]->(x:Pattern) "
                                     "RETURN x.name AS pattern, x.meaning AS meaning, h.positions AS positions, "
                                     "h.share AS share_of_all, h.win_pct AS win_pct, h.pnl AS total_pnl "
                                     "ORDER BY h.pnl", {"w": w}),
                "by_state": _rows(c, "MATCH (:Wallet {address: $w})-[:TRADED]->(p:Position)-[:OPENED_IN]->(s:State) "
                                     "WITH s, count(p) AS n, sum(CASE WHEN p.won THEN 1 ELSE 0 END) AS wins, sum(p.pnl) AS pnl "
                                     "RETURN s.name AS state, n AS positions, round(100.0 * wins / n, 1) AS win_pct, "
                                     "round(pnl, 2) AS total_pnl ORDER BY s.rank", {"w": w}),
            }
        return with_store(run)

    @app.tool()
    def peers(wallet: str = "") -> dict:
        """The wallets that behave most like this one (vector search over habit vectors, kept as
        PEER_OF edges) and how they did."""
        def run(c):
            w = guard._wallet(c, wallet or None)
            return {"wallet": w, "peers": _rows(
                c, "MATCH (:Wallet {address: $w})-[e:PEER_OF]->(p:Wallet) "
                   "OPTIONAL MATCH (p)-[h:HAS_HABIT]->(:Pattern {name: 'revenge'}) "
                   "RETURN p.address AS wallet, e.score AS similarity, p.positions AS positions, "
                   "p.win_pct AS win_pct, p.pnl AS pnl, h.share AS revenge_share, h.pnl AS revenge_pnl "
                   "ORDER BY e.score DESC", {"w": w})}
        return with_store(run)

    @app.tool()
    def spirals(wallet: str = "", limit: int = 5) -> dict:
        """The worst loss spirals of a wallet: chains of positions, each opened within 30 minutes
        after the previous loss. A path query over TILTED_FROM."""
        def run(c):
            w = guard._wallet(c, wallet or None)
            return {"wallet": w, "spirals": _rows(c, """
                MATCH (:Wallet {address: $w})-[:TRADED]->(last:Position)
                WHERE EXISTS { (last)-[:TILTED_FROM]->(:Position) }
                  AND NOT EXISTS { (:Position)-[:TILTED_FROM]->(last) }
                MATCH path = (last)-[:TILTED_FROM]->+(first:Position)
                WHERE NOT EXISTS { (first)-[:TILTED_FROM]->(:Position) }
                WITH nodes(path) AS ns
                RETURN size(ns) AS positions, round(reduce(s = 0.0, n IN ns | s + n.pnl), 2) AS pnl,
                       [n IN reverse(ns) | n.coin + ' ' + n.side + ' ' + toString(round(n.pnl, 2))] AS path,
                       toString(datetime({epochMillis: ns[-1].opened_at})) AS started
                ORDER BY pnl LIMIT $k""", {"w": w, "k": int(limit)})}
        return with_store(run)

    @app.tool()
    def ask(question: str) -> dict:
        """Run one of the prepared questions. Names starting with study_ look across all wallets.
        Returns the rows and the Cypher. Call with question="list" to see the names."""
        if question not in report.ALL:
            return {"questions": {k: v[0] for k, v in report.ALL.items()}}
        def run(c):
            title, query, rows = report.run(c, question)
            return {"question": title, "rows": json.loads(json.dumps(rows, default=str)),
                    "cypher": query.strip()}
        return with_store(run)

    @app.tool()
    def schema() -> str:
        """The shape of the graph. Read it before writing your own Cypher."""
        return SCHEMA_TEXT

    @app.tool()
    def read_cypher(query: str, limit: int = 50) -> dict:
        """Run your own read-only Cypher. The store is opened read-only, writes fail."""
        def run(c):
            rows = _rows(c, query)
            return {"rows": rows[:limit], "truncated": len(rows) > limit}
        return with_store(run)

    return app


def serve(database):
    build(database).run()

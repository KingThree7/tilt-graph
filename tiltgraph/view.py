"""One self-contained HTML page: the report, and each spiral drawn as a chain."""
import html as h

from . import report

CSS = """
:root{--bg:#fbfaf7;--ink:#1c1b19;--mute:#6b675f;--line:#e2ddd3;--loss:#b3402e;--win:#2f7d55;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161513;--ink:#eeeae2;--mute:#a09a8e;--line:#34312c;--loss:#e07a66;--win:#6cc194;--card:#1f1d1a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 ui-sans-serif,system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:32px 16px 64px}h1{font-size:28px;margin:0 0 4px}
h2{font-size:18px;margin:36px 0 10px}p.sub{color:var(--mute);margin:0 0 8px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px}
th,td{padding:8px 12px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{font-size:13px;color:var(--mute);font-weight:600}tr:last-child td{border-bottom:0}
.neg{color:var(--loss)}.pos{color:var(--win)}
.chain{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:10px 0;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:8px}
.node{padding:6px 10px;border-radius:6px;border:1px solid var(--loss);font-size:14px}.node b{display:block}
.node.win{border-color:var(--win)}.gap{color:var(--mute);font-size:13px}.total{margin-left:auto;font-weight:600}
"""


def _cell(v):
    if isinstance(v, float) or isinstance(v, int) and not isinstance(v, bool):
        cls = ' class="neg"' if v < 0 else ""
        return f"<td{cls}>{v:,}</td>" if isinstance(v, int) else f"<td{cls}>{v:,.2f}</td>"
    return f"<td>{h.escape(str(v))}</td>"


def _table(rows):
    if not rows:
        return "<p class=sub>No rows.</p>"
    cols = list(rows[0].keys())
    head = "".join(f"<th>{h.escape(c.replace('_', ' '))}</th>" for c in cols)
    body = "".join("<tr>" + "".join(_cell(r[c]) for c in cols) + "</tr>" for r in rows)
    return f"<div class=scroll><table><tr>{head}</tr>{body}</table></div>"


SPIRAL_DETAIL = """
MATCH path = (last:Position)-[:OPENED_AFTER]->+(first:Position)
WHERE all(r IN relationships(path) WHERE r.gap_min <= 30)
  AND all(n IN nodes(path)[1..] WHERE n.won = false)
  AND NOT EXISTS { MATCH (later:Position)-[r2:OPENED_AFTER]->(last)
                   WHERE r2.gap_min <= 30 AND last.won = false }
  AND NOT EXISTS { MATCH (first)-[r3:OPENED_AFTER]->(before:Position)
                   WHERE r3.gap_min <= 30 AND before.won = false }
WITH nodes(path) AS ns, relationships(path) AS rs
RETURN [n IN reverse(ns) | {coin: n.coin, side: n.side, pnl: n.pnl, won: n.won}] AS steps,
       [r IN reverse(rs) | r.gap_min] AS gaps,
       reduce(s = 0.0, n IN ns | s + n.pnl) AS pnl
ORDER BY pnl LIMIT 5
"""


def _spirals(conn):
    out = []
    for row in conn.execute(SPIRAL_DETAIL):
        steps, gaps, total = row["steps"], row["gaps"], row["pnl"]
        parts = []
        for i, s in enumerate(steps):
            if i:
                parts.append(f"<span class=gap>{gaps[i - 1]:.0f} min later &rarr;</span>")
            cls = "node win" if s["won"] else "node"
            parts.append(f"<span class='{cls}'><b>{h.escape(s['coin'])} {h.escape(s['side'])}</b>{s['pnl']:,.2f}</span>")
        parts.append(f"<span class='total neg'>{total:,.2f}</span>")
        out.append("<div class=chain>" + "".join(parts) + "</div>")
    return "".join(out) or "<p class=sub>No spiral found.</p>"


def html(conn):
    row = conn.execute("MATCH (w:Wallet) RETURN w.address AS a LIMIT 1").fetchone()
    address = row["a"] if row else "?"
    parts = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>Tilt Graph</title><style>{CSS}</style><main><h1>Tilt Graph</h1>"
             f"<p class=sub>What this trader does right after a loss. Wallet {h.escape(address)}</p>"]
    for name in report.QUESTIONS:
        title, _, rows = report.run(conn, name)
        parts.append(f"<h2>{h.escape(title)}</h2>")
        parts.append(_spirals(conn) if name == "spirals" else _table(rows))
    parts.append("</main></html>")
    return "".join(parts)

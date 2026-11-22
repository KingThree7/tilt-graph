"""One self-contained HTML page of what the graph holds. No server, no script, no network."""
import html as _html

from . import guard, report

CSS = """
:root{--bg:#fbfaf7;--ink:#1c1b19;--mute:#6b675f;--line:#e2ded5;--card:#fff;--bad:#b3261e;--good:#1f7a4d;
--warn:#a35f00;--accent:#2f4f8f}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--ink:#ece8df;--mute:#a09a8e;--line:#34312b;
--card:#1e1c19;--bad:#ff8a80;--good:#7bd3a4;--warn:#f0b35a;--accent:#9db8f2}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:34px;line-height:1.15;margin:0 0 8px;letter-spacing:-.02em}
h2{font-size:21px;margin:44px 0 6px}p.lead{color:var(--mute);margin:0 0 16px;max-width:70ch}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card b{display:block;font-size:26px;letter-spacing:-.01em}.card span{color:var(--mute);font-size:13px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:8px 12px;text-align:right;
border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--mute);font-weight:600;font-size:12px;
text-transform:uppercase;letter-spacing:.04em}td:first-child,th:first-child{text-align:left}
tr:last-child td{border-bottom:0}td.wrap{white-space:normal;min-width:230px;text-align:left}.neg{color:var(--bad)}.pos{color:var(--good)}
.chip{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600;
border:1px solid currentColor}.solidbad{color:var(--bad)}.solidgood{color:var(--good)}.weak{color:var(--warn)}
.none{color:var(--mute)}.chain{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:8px 0 14px}
.node{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:6px 10px;font-size:13px}
.node small{display:block;color:var(--mute)}.arrow{color:var(--mute)}
.bar{display:grid;grid-template-columns:90px 1fr 70px;gap:10px;align-items:center;font-size:13px;margin:5px 0}
.bar i{display:block;height:14px;border-radius:4px;background:var(--accent)}
code,pre{font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;overflow-x:auto}
footer{margin-top:48px;color:var(--mute);font-size:13px}
"""


def _e(v):
    return _html.escape(str(v))


def _num(v, digits=1):
    if v is None:
        return "–"
    cls = "neg" if isinstance(v, (int, float)) and v < 0 else ""
    text = f"{v:,.{digits}f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else _e(v)
    return f'<span class="{cls}">{text}</span>'


def _table(rows, cols):
    """cols: list of (key, header, digits)."""
    if not rows:
        return "<p class='lead'>No rows.</p>"
    out = ["<div class='scroll'><table><tr>" + "".join(f"<th>{_e(h)}</th>" for _, h, _ in cols) + "</tr>"]
    for r in rows:
        out.append("<tr>" + "".join(
            (f"<td class='wrap'>{r[k]}</td>" if k.endswith('_html') else f"<td>{_num(r.get(k), d)}</td>")
            for k, _, d in cols) + "</tr>")
    return "".join(out) + "</table></div>"


def _chip(grade):
    grade = grade or "untested"
    cls = ("solidbad" if grade.startswith("solid: costs") else "solidgood" if grade.startswith("solid")
           else "weak" if grade.startswith("weak") else "none")
    return f'<span class="chip {cls}">{_e(grade.split(":")[0])}</span><br><span class="none">{_e(grade.partition(": ")[2])}</span>'


def _rows(conn, query, params=None):
    return [dict(r) for r in conn.execute(query, params or {})]


def html(conn, wallet=None):
    size = _rows(conn, "MATCH (w:Wallet) OPTIONAL MATCH (w)-[:TRADED]->(p:Position) "
                       "RETURN count(DISTINCT w) AS wallets, count(p) AS positions, "
                       "sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) AS liq, round(sum(p.pnl), 0) AS pnl")[0]
    adds = _rows(conn, "MATCH (a:Add) RETURN count(a) AS n")[0]["n"]
    many = size["wallets"] > 1
    parts = [f"<!doctype html><html lang='en'><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Tilt Graph</title><style>{CSS}</style><main>",
             "<h1>Tilt Graph</h1>",
             "<p class='lead'>A trading journal kept as a graph. It links every position to what came before "
             "it, tests which habits really cost money, and checks its own advice against what happened next.</p>",
             "<div class='cards'>"
             f"<div class='card'><b>{size['wallets']:,}</b><span>wallet{'s' if many else ''}</span></div>"
             f"<div class='card'><b>{size['positions']:,}</b><span>closed positions</span></div>"
             f"<div class='card'><b>{adds:,}</b><span>adds made under water</span></div>"
             f"<div class='card'><b>{size['liq']:,}</b><span>liquidations</span></div>"
             f"<div class='card'><b class='{'neg' if size['pnl'] < 0 else 'pos'}'>{size['pnl']:,.0f}</b>"
             "<span>net result, USDC</span></div></div>"]

    # 1. Evidence
    ev = _rows(conn, "MATCH (x:Pattern) RETURN x.name AS pattern, x.meaning AS meaning, x.grade AS grade, "
                     "x.tested_wallets AS wallets, x.win_pts AS win_pts, x.ret_bps AS ret_bps, "
                     "x.later_half_ret_bps AS later, x.share_of_positions_pct AS share, "
                     "x.share_of_net_result_pct AS loss_share, x.evidence_from AS src ORDER BY x.ret_bps")
    for r in ev:
        r["name_html"] = f"<b>{_e(r['pattern'])}</b><br><span class='none'>{_e(r['meaning'])}</span>"
        r["grade_html"] = _chip(r["grade"])
    src = next((r["src"] for r in ev if r["src"]), "this store")
    parts += ["<h2>Which habits cost money</h2>",
              "<p class='lead'>Each wallet is compared with itself: its positions that show a habit against "
              "its other positions. A habit is called solid only if the difference also holds on the later "
              f"half of every wallet's history. Evidence from: {_e(src)}.</p>",
              _table(ev, [("name_html", "Habit", 0), ("grade_html", "Evidence", 0), ("wallets", "Wallets", 0),
                          ("win_pts", "Win rate", 1), ("ret_bps", "Return bps", 1),
                          ("later", "Later data bps", 1), ("share", "% of positions", 1),
                          ("loss_share", "% of net result", 1)])]

    # 2. The guard's own track record
    track = _rows(conn, "MATCH (v:Verdict) WHERE v.positions IS NOT NULL RETURN v.name AS said, "
                        "v.positions AS positions, v.win_pct AS win_pct, v.avg_return_bps AS ret, "
                        "v.liquidated_per_1000 AS liq, v.rule AS rule ORDER BY v.name")
    if track:
        parts += ["<h2>Does the guard's advice hold up</h2>",
                  f"<p class='lead'>The rule was learned on the first half of every wallet's history "
                  f"(it came out as: <b>{_e(track[0]['rule'] or 'nothing')}</b>). Then every position in the "
                  "second half got the verdict that rule gives. Advice and outcome sit side by side in the "
                  "graph as <code>(:Position)-[:GUARD_SAID]-&gt;(:Verdict)</code>.</p>",
                  _table(track, [("said", "Guard said", 0), ("positions", "Positions", 0), ("win_pct", "Win %", 1),
                                 ("ret", "Avg return, bps", 1), ("liq", "Liquidated per 1,000", 1)])]

    # 3. Ladders
    ladder = _rows(conn, """
        MATCH path = (p:Position)-[:AVERAGED_DOWN]->(:Add)-[:THEN]->*(last:Add)
        WHERE NOT EXISTS { (last)-[:THEN]->(:Add) }
        WITH CASE WHEN length(path) >= 10 THEN 10 ELSE length(path) END AS rungs, p
        RETURN rungs, count(p) AS positions,
               1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p) AS liq
        ORDER BY rungs""")
    if ladder:
        top = max(r["liq"] for r in ladder) or 1
        parts += ["<h2>The ladder</h2>",
                  "<p class='lead'>Every add made with the price at least 1% past the average entry is a node. The adds "
                  "of one position form a chain. How deep the chain went, against how often the exchange "
                  "closed the position by force (liquidations per 1,000 positions).</p>"]
        for r in ladder:
            label = f"{r['rungs']}{'+' if r['rungs'] == 10 else ''} add{'s' if r['rungs'] > 1 else ''}"
            parts.append(f"<div class='bar'><span>{label}</span><span><i style='width:{100 * r['liq'] / top:.0f}%'>"
                         f"</i></span><span>{r['liq']:.0f} <span class='none'>/ {r['positions']:,}</span></span></div>")

    # 4. One wallet
    try:
        w = guard._wallet(conn, wallet)
    except ValueError:
        w = _rows(conn, "MATCH (w:Wallet)-[h:HAS_HABIT]->(:Pattern {name: 'average_down'}) "
                        "WHERE h.positions >= 15 RETURN w.address AS a ORDER BY h.pnl LIMIT 1")
        w = w[0]["a"] if w else None
    if w:
        habits = _rows(conn, "MATCH (:Wallet {address: $w})-[h:HAS_HABIT]->(x:Pattern) "
                             "RETURN x.name AS pattern, h.positions AS n, 100 * h.share AS share, "
                             "h.win_pct AS win, h.pnl AS pnl ORDER BY h.pnl", {"w": w})
        peers = _rows(conn, "MATCH (:Wallet {address: $w})-[e:PEER_OF]->(p:Wallet) "
                            "OPTIONAL MATCH (p)-[h:HAS_HABIT]->(:Pattern {name: 'average_down'}) "
                            "RETURN p.address AS wallet, e.score AS score, p.positions AS n, p.win_pct AS win, "
                            "p.pnl AS pnl, h.pnl AS down ORDER BY e.score DESC", {"w": w})
        parts += [f"<h2>One wallet: <code>{_e(w)}</code></h2>",
                  "<p class='lead'>Its habits, as edges from the wallet to each pattern.</p>",
                  _table(habits, [("pattern", "Habit", 0), ("n", "Times", 0), ("share", "% of its positions", 1),
                                  ("win", "Win %", 1), ("pnl", "Total result", 0)])]
        worst = _rows(conn, """
            MATCH (:Wallet {address: $w})-[:TRADED]->(p:Position)-[:AVERAGED_DOWN]->(first:Add)
            WITH p, first ORDER BY p.pnl LIMIT 3
            MATCH path = (first)-[:THEN]->*(last:Add) WHERE NOT EXISTS { (last)-[:THEN]->(:Add) }
            RETURN p.coin AS coin, p.side AS side, p.pnl AS pnl, p.liquidated AS liq, p.adds_against AS adds,
                   [a IN nodes(path) | a.pct_from_entry] AS steps ORDER BY p.pnl""", {"w": w})
        if worst:
            parts.append("<p class='lead'>Its three most expensive ladders. Each box is one add, with how far "
                         "the price was from the average entry at that moment.</p>")
            for r in worst:
                boxes = "<span class='arrow'>→</span>".join(
                    f"<span class='node'>add {i}<small>{s:.1f}% under water</small></span>"
                    for i, s in enumerate(r["steps"], 1))
                more = f" <span class='none'>(+{r['adds'] - len(r['steps'])} more)</span>" if r["adds"] > len(r["steps"]) else ""
                end = "liquidated" if r["liq"] else "closed"
                parts.append(f"<div class='chain'><span class='node'><b>{_e(r['coin'])} {_e(r['side'])}</b>"
                             f"<small>opened</small></span><span class='arrow'>→</span>{boxes}{more}"
                             f"<span class='arrow'>→</span><span class='node'><b class='neg'>{r['pnl']:,.0f}</b>"
                             f"<small>{end}</small></span></div>")
        spirals = [s for s in report.run(conn, "spirals")[2] if s["wallet"] == w][:3] if not many else _rows(conn, """
            MATCH (:Wallet {address: $w})-[:TRADED]->(last:Position)
            WHERE EXISTS { (last)-[:TILTED_FROM]->(:Position) } AND NOT EXISTS { (:Position)-[:TILTED_FROM]->(last) }
            MATCH path = (last)-[:TILTED_FROM]->+(first:Position)
            WHERE NOT EXISTS { (first)-[:TILTED_FROM]->(:Position) }
            WITH nodes(path) AS ns
            RETURN size(ns) AS positions, round(reduce(s = 0.0, n IN ns | s + n.pnl), 2) AS pnl,
                   [n IN reverse(ns) | n.coin + ' ' + n.side] AS sequence ORDER BY pnl LIMIT 3""", {"w": w})
        if spirals:
            parts.append("<p class='lead'>Its worst loss spirals: positions each opened within 30 minutes after "
                         "the previous loss. The evidence says this habit lowers the win rate a little and "
                         "does not, on average, cost money. It is shown, not judged.</p>")
            for s in spirals:
                boxes = "<span class='arrow'>→</span>".join(f"<span class='node'>{_e(x)}</span>" for x in s["sequence"])
                parts.append(f"<div class='chain'>{boxes}<span class='arrow'>=</span>"
                             f"<span class='node'><b class='neg'>{s['pnl']:,.0f}</b></span></div>")
        if peers:
            parts += ["<p class='lead'>The wallets that behave most like it, found by vector search over habit "
                      "vectors and kept as <code>PEER_OF</code> edges.</p>",
                      _table(peers, [("wallet", "Peer", 0), ("score", "Similarity", 3), ("n", "Positions", 0),
                                     ("win", "Win %", 1), ("pnl", "Result", 0), ("down", "Result when averaging down", 0)])]

    # 5. States
    states = _rows(conn, "MATCH (p:Position)-[:OPENED_IN]->(s:State) WITH s, count(p) AS n, "
                         "100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p) AS win, "
                         "1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p) AS liq "
                         "RETURN s.name AS state, n, win, liq ORDER BY s.rank")
    parts += ["<h2>The state a position was opened in</h2>",
              "<p class='lead'>All wallets pooled. This is the table that makes revenge trading look terrible. "
              "Pooled numbers mix two things: what a habit does, and who has the habit. The evidence table "
              "above is the fair version.</p>",
              _table(states, [("state", "Opened", 0), ("n", "Positions", 0), ("win", "Win %", 1),
                              ("liq", "Liquidated per 1,000", 1)])]
    parts.append("<footer>Built with Relate, an embedded graph database. One file holds everything on this "
                 "page. Past behaviour, not a forecast, and not advice.</footer></main></html>")
    return "".join(parts)

# For an agent reading a Tilt Graph store

The store is one Relate file (`.rl`). Open it read-only. Serve it to an agent host with
`relate serve --mcp stdio --database examples/demo.rl --read-only`, then use `get_schema`
and `read_cypher`.

## Shape

- `(:Wallet {address})-[:TRADED]->(:Position)`
- `(:Position)-[:ON]->(:Coin {name})`, `(:Position)-[:ON_DAY]->(:Day {date})`
- `(:Position)-[:NEXT]->(:Position)`: the next position by opening time.
- `(a:Position)-[:OPENED_AFTER {gap_min, size_ratio}]->(b:Position)`: `b` is the last position
  that had closed when `a` opened. `gap_min` is the wait in minutes. `size_ratio` is the size of
  `a` divided by the size of `b`.
- `(:Position)-[:SHOWS]->(:Pattern {name, meaning})`: `revenge`, `size_up`, `flip`, `chase`.

`Position` properties: `id, coin, side, opened_at, closed_at` (ms since epoch), `pnl` (after
fees, USDC), `fees, max_notional, fills, adds, taker_share, minutes, won, hour` (UTC).

## Before the trader opens something

Find the last closed position and how long ago it closed. Then ask how positions opened in
the same state went before:

```cypher
MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position)
WHERE prev.won = false AND r.gap_min <= 15
RETURN count(p) AS cases,
       round(100.0 * sum(CASE WHEN p.won THEN 1 ELSE 0 END) / count(p), 1) AS win_pct,
       round(avg(p.pnl), 2) AS avg_pnl
```

Say the numbers and the number of cases. With fewer than eight cases, say that it is too few
to judge. Describe what happened; do not predict prices and do not give trading advice.

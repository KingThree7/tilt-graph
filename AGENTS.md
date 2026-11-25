# For an agent using a Tilt Graph store

The store is one Relate file (`.rl`). Use it read-only, through the MCP server:

```bash
tiltgraph serve -d examples/cohort.rl
```

## Before a trade

Call `pre_trade_check` with the wallet and, if you know them, coin, side and size. Set
`adding_to_loser` when the trade adds to an open position that is under water, or `live` to let
the tool look that up for a real address.

- `WAIT`: say so, give the reason and the numbers, and do not place the trade unless the
  person insists.
- `GO`: no habit with solid evidence applies. This is not a recommendation to trade.
- `UNKNOWN`: the history or the evidence is too thin. Never turn `UNKNOWN` into `GO`.

The answer also carries `similar_moments`: the positions opened in the most similar situation
(vector search over `Position.moment`) and how they ended. Say it as a description ("the 200
closest moments ended like this"), never as a reason for `WAIT` or `GO`. Use `find_question`
to look up a prepared question by a few words, then `ask` to run it.

Read `evidence.grade` before you speak about a habit:

| Grade starts with | Say |
|---|---|
| `solid` | what was measured, on how many wallets, and that it held on later data |
| `weak` | that the win rate is a little lower and that no cost was measured |
| `none` | that no reliable difference was found |
| `untested` | that this store holds no test for it |

Do not call a `weak` habit harmful. In the bundled cohort, trading right after a loss is
`weak`: fewer wins, no measurable cost.

## Shape

```
(:Wallet {address, positions, win_pct, pnl, liquidations, habit})
(:Position {id, wallet, coin, side, opened_at, closed_at, minutes, pnl, ret, won, max_notional,
            adds, adds_against, first_add_pct, worst_move_pct, taker_share, liquidated, hour, loss_streak, moment})
(:Pattern {name, meaning, grade, wallets, compared, win_pts, ret_bps, ret_t, liq_per_1000,
           liq_t, later_ret_bps, later_liq_per_1000, evidence_from})
(:Verdict {name, positions, win_pct, avg_return_bps, liquidated_per_1000, rule})

(:Wallet)-[:TRADED]->(:Position)-[:ON]->(:Coin)
(:Position)-[:OPENED_AFTER {gap_min, size_ratio}]->(:Position)   the position that had just closed
(:Position)-[:TILTED_FROM]->(:Position)                          opened within 30 min after that loss
(:Position)-[:OPENED_IN]->(:State)
(:Position)-[:SHOWS]->(:Pattern)        revenge, size_up, flip, chase, average_down
(:Position)-[:AVERAGED_DOWN]->(:Add)-[:THEN]->(:Add)             adds made under water, in order
(:Wallet)-[:HAS_HABIT {positions, share, win_pct, avg_ret, pnl}]->(:Pattern)
(:Wallet)-[:PEER_OF {score}]->(:Wallet)
(:Position)-[:GUARD_SAID {because}]->(:Verdict)                  replayed advice next to the outcome
```

Times are milliseconds since epoch, UTC. `pnl` is USDC after fees. `ret` is `pnl / max_notional`.
`worst_move_pct` is how far the price went against the position at its worst.

## Useful statements

A wallet's habits with the evidence about each:

```cypher
MATCH (w:Wallet {address: $w})-[h:HAS_HABIT]->(x:Pattern)
RETURN x.name, x.grade, h.positions, h.win_pct, h.pnl ORDER BY h.pnl
```

The loss chain a wallet is in:

```cypher
MATCH path = (last:Position {id: $id})-[:TILTED_FROM]->*(first:Position)
RETURN [n IN nodes(path) | n.coin + ' ' + n.side] AS chain ORDER BY length(path) DESC LIMIT 1
```

How the guard's advice did on data it had not seen:

```cypher
MATCH (p:Position)-[:GUARD_SAID]->(v:Verdict)
RETURN v.name, count(p), round(avg(p.ret) * 10000) AS avg_return_bps,
       round(1000.0 * sum(CASE WHEN p.liquidated THEN 1 ELSE 0 END) / count(p)) AS liquidated_per_1000
```

## How to speak

Give the numbers and the number of cases. Say what was measured and what was not. Describe
past behaviour; do not predict a price and do not give trading advice. Wallets in the cohort
store are aliases; do not try to resolve them.

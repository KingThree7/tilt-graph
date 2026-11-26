# An agent asks before the order

This is a real session, not a mock-up. The agent was Claude (Anthropic). It was connected to the
cohort store through the Tilt Graph MCP server over stdio, with
[`mcp_call.py`](mcp_call.py) as the client, and every block below is the unedited answer of
the server, shortened only where marked. The store was opened read-only.

**The setting.** Wallet `w_5c4f89f6` holds a BTC short that is more than 1% under water. On
19 October 2026 at 06:22 UTC it is about to add 4,999 USDC to it. We put the agent at that moment: asked to
place the order, it checks first. We picked the moment afterwards, knowing how it ended, to
show what the agent would have seen. The fair measure of the guard is the audit table in the
README, not this one case.

## 1. The check

```bash
python examples/mcp_call.py examples/cohort.rl pre_trade_check \
  '{"wallet": "w_5c4f89f6", "coin": "BTC", "side": "short", "notional": 4999,
    "adding_to_loser": true, "at_ms": 1792390973465}'
```

```json
{
 "verdict": "WAIT",
 "state": "calm",
 "last_closed": {
  "id": "w_5c4f89f6-xyz:SKHX-1792370553806",
  "coin": "xyz:SKHX",
  "side": "long",
  "pnl": 197.01,
  "minutes_ago": 274.5
 },
 "planned_trade_matches": [
  {
   "pattern": "average_down",
   "evidence": {
    "grade": "solid: more liquidations, also on later data; average return unchanged",
    "wallets_tested": 1122,
    "compared": "against the same wallet's positions that were equally far under water and were not added to",
    "win_rate_change_pts": 3.9,
    "return_change_bps": 8.7,
    "return_t": 1.3,
    "liquidations_change_per_1000": 53.5,
    "liquidations_t": 10.8,
    "on_later_data": {
     "return_change_bps": -7.1,
     "liquidations_change_per_1000": 63.0
    },
    "from": "this store: 2305 wallets"
   },
   "you": {
    "times": 38,
    "win_pct": 50.0,
    "total_pnl": 12581.18
   },
   "your_peers": {
    "peers": 8,
    "times": 496,
    "total_pnl": -50540.14,
    "peers_who_lost_on_it": 8
   }
  }
 ],
 "reason": "This trade would be 'average_down': Added to the position while it was at least 1% under water. Tested on 1122 wallets, against the same wallet's positions that were equally far under water and were not added to: +54 liquidations per 1,000 positions (+63 on later data), with no reliable change in average return (+9 basis points). More small wins, more blow-ups. You did this 38 times: total result +12581.18.",
 "similar_moments": {
  "all_wallets": {
   "moments": 200,
   "wallets": 139,
   "win_pct": 56.0,
   "avg_return_bps": 26.0,
   "liquidated_per_1000": 45.0,
   "later_added_under_water_pct": 5.5,
   "least_similar_score": 0.995
  },
  "every_position_before_now": {
   "positions": 222834,
   "win_pct": 45.4,
   "avg_return_bps": 8.0,
   "liquidated_per_1000": 35.0
  },
  "you": {
   "moments": 20,
   "win_pct": 50.0,
   "avg_return_bps": 34.0,
   "total_pnl": -764.97,
   "liquidated": 0,
   "least_similar_score": 0.576
  }
 },
 "guard_track_record": [
  {
   "said": "GO",
   "positions": 132409,
   "win_pct": 44.7,
   "avg_return_bps": -19.3,
   "liquidated_per_1000": 41.0
  },
  {
   "said": "WAIT",
   "positions": 25534,
   "win_pct": 39.5,
   "avg_return_bps": -37.4,
   "liquidated_per_1000": 85.1
  }
 ]
}
```

(Left out here: the wording of each habit, the description of the moment as seven numbers, and
the Cypher behind the evidence, which the server returns with every answer.)

**What the agent said to the trader:**

> I am holding this order back. Adding to a position that is under water is one of the two
> habits with solid evidence in this store. In 1,122 wallets, positions that were added to at
> this depth were liquidated more often than the same wallet's positions that were left alone: 54 more
> per 1,000, and 63 more on later data. The average return was not worse, so
> I am not saying this add loses money. I am saying it raises the chance that the position is
> closed by force.
>
> Your own record speaks for you: you added under water 38 times before today and made
> 12,581 USDC with it. The eight wallets that trade most like you did it 496 times over the
> whole period and all eight lost on it, 50,540 USDC together. The 200 most similar moments
> in the store won more often than average and were liquidated a little more often, so the
> moment itself is no warning. The warning is the add.
>
> If you still want it, say so and I will place it.

## 2. The agent looks closer

```bash
python examples/mcp_call.py examples/cohort.rl habits '{"wallet": "w_5c4f89f6"}'
```

The wallet's habits over the whole period (shortened to two rows):

```json
[
 {
  "pattern": "average_down",
  "meaning": "Added to the position while it was at least 1% under water.",
  "positions": 57,
  "share_of_all": 0.197,
  "win_pct": 47.4,
  "total_pnl": 14636.06
 },
 {
  "pattern": "revenge",
  "meaning": "Opened within 15 minutes after closing a loss.",
  "positions": 75,
  "share_of_all": 0.26,
  "win_pct": 41.3,
  "total_pnl": 32620.48
 }
]
```

This is a trader for whom fast re-entries after a loss worked, which fits the evidence: that
habit is graded weak, and the guard never warns about it.

## 3. What happened

In the real history there was no agent, and the trader added. The agent can ask the graph how
it ended, with its own Cypher:

```bash
python examples/mcp_call.py examples/cohort.rl read_cypher '{"query": "
  MATCH (p:Position {wallet: \"w_5c4f89f6\", coin: \"BTC\"})-[:AVERAGED_DOWN]->(first:Add {at: 1792390973465})
  MATCH path = (first)-[:THEN]->*(last:Add) WHERE NOT EXISTS { (last)-[:THEN]->(:Add) }
  MATCH (p)-[g:GUARD_SAID]->(v:Verdict)
  RETURN p.side AS side, length(path) + 1 AS adds_under_water,
         last.pct_from_entry AS last_add_pct_from_entry, round(p.minutes / 60.0, 1) AS hours_open,
         round(p.pnl, 2) AS pnl, p.liquidated AS liquidated, v.name AS guard_said, g.because AS because"}'
```

```json
[
 {
  "side": "short",
  "adds_under_water": 12,
  "last_add_pct_from_entry": 2.015,
  "hours_open": 100.8,
  "pnl": -3219.91,
  "liquidated": true,
  "guard_said": "WAIT",
  "because": "average_down"
 }
]
```

Twelve adds under water over four days, then the exchange closed the position: 3,220 USDC
lost. The ladder is a path of `Add` nodes, and the replayed advice sits on the same position as
a `GUARD_SAID` edge, so advice and outcome are one query apart.

## The same gate in code

[`order_gate.py`](order_gate.py) does step 1 without a language model: it runs the check and
holds the order back unless the answer is `GO`. It never places an order.

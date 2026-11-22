from tiltgraph import candles, graph, guard, positions, report, sample, study

A = "0xabc0000000"


def f(coin, side, px, sz, start, pnl, t, tid, crossed=True, fee=0.0):
    return {"coin": coin, "px": str(px), "sz": str(sz), "side": side, "time": t,
            "startPosition": str(start), "closedPnl": str(pnl), "crossed": crossed,
            "fee": str(fee), "tid": tid}


def test_flat_to_flat_is_one_position():
    fills = [f("BTC", "B", 100, 1, 0, 0, 1000, 1, fee=0.1),
             f("BTC", "B", 100, 1, 1, 0, 2000, 2, fee=0.1),
             f("BTC", "A", 110, 2, 2, 20, 3000, 3, fee=0.2)]
    (p,) = positions.build_positions(A, fills)
    assert (p.side, p.fills, p.adds) == ("long", 3, 1)
    assert p.pnl == 19.6 and p.fees == 0.4 and p.max_notional == 220


def test_one_fill_can_close_and_reverse():
    fills = [f("ETH", "B", 10, 1, 0, 0, 1000, 1),
             f("ETH", "A", 9, 3, 1, -1, 2000, 2),   # closes the long, opens a short of 2
             f("ETH", "B", 8, 2, -2, 2, 3000, 3)]
    a, b = positions.build_positions(A, fills)
    assert (a.side, a.pnl) == ("long", -1.0)
    assert (b.side, b.opened_at, b.closed_at) == ("short", 2000, 3000)


def test_position_opened_before_the_window_is_left_out():
    fills = [f("SOL", "A", 5, 1, 4, 3, 1000, 1), f("SOL", "A", 5, 3, 3, 9, 2000, 2),
             f("SOL", "B", 5, 1, 0, 0, 3000, 3), f("SOL", "A", 6, 1, 1, 1, 4000, 4)]
    (p,) = positions.build_positions(A, fills)
    assert p.opened_at == 3000


def test_still_open_position_and_spot_are_left_out():
    fills = [f("@107", "B", 1, 1, 0, 0, 1000, 1), f("BTC", "B", 100, 1, 0, 0, 2000, 2)]
    assert positions.build_positions(A, fills) == []


def _worst(fills, pos):
    return candles.worst_moves(candles.from_fills(fills), pos)


def _store():
    conn = graph.open_store(":memory:")
    fills = sample.make_fills()
    pos = positions.build_positions(sample.ADDRESS, fills)
    graph.write(conn, sample.ADDRESS, pos, worst=_worst(fills, pos))
    return conn, pos


def _counts(conn):
    n = conn.execute("MATCH (n) RETURN count(n) AS c").single()["c"]
    r = conn.execute("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    return n, r


def test_writing_twice_changes_nothing():
    conn, pos = _store()
    before = _counts(conn)
    graph.write(conn, sample.ADDRESS, pos)
    assert _counts(conn) == before


def test_opened_after_points_at_the_last_position_closed_before():
    conn, _ = _store()
    bad = conn.execute(
        "MATCH (p:Position)-[r:OPENED_AFTER]->(prev:Position) "
        "WHERE prev.closed_at > p.opened_at OR r.gap_min < 0 RETURN count(*) AS c").single()["c"]
    assert bad == 0
    skipped = conn.execute(
        "MATCH (p:Position)-[:OPENED_AFTER]->(prev:Position), (other:Position) "
        "WHERE other.closed_at > prev.closed_at AND other.closed_at <= p.opened_at AND other <> p "
        "RETURN count(*) AS c").single()["c"]
    assert skipped == 0


def test_report_finds_the_planted_habit():
    conn, _ = _store()
    rows = {r["pattern"]: r for r in report.run(conn, "patterns")[2]}
    assert rows["revenge"]["win_pct"] < rows["calm (no pattern)"]["win_pct"] - 10
    assert rows["revenge"]["total_pnl"] < 0
    spirals = report.run(conn, "spirals")[2]
    assert spirals and spirals[0]["pnl"] < 0 and max(r["positions"] for r in spirals) >= 3
    assert rows["average_down"]["total_pnl"] < rows["revenge"]["total_pnl"]


def test_thresholds_can_be_changed_without_new_data():
    conn, pos = _store()
    q = "MATCH (:Position)-[:SHOWS]->(:Pattern {name: 'revenge'}) RETURN count(*) AS c"
    wide = conn.execute(q).single()["c"]
    graph.write(conn, sample.ADDRESS, pos, window=3)
    assert 0 < conn.execute(q).single()["c"] < wide


def test_guard_waits_only_for_a_habit_with_solid_evidence():
    conn, _ = _store()   # one wallet: evidence comes from the bundled cohort study
    lost = conn.execute("MATCH (p:Position) WHERE p.won = false RETURN p.closed_at AS t, p.coin AS coin "
                        "ORDER BY t DESC LIMIT 1").single()
    now = lost["t"] + 3 * 60000
    add = guard.check(conn, now, adding_to_loser=True)
    assert add["verdict"] == "WAIT"
    assert add["planned_trade_matches"][0]["evidence"]["grade"].startswith("solid")
    assert add["guard_track_record"]["rows"]
    # revenge alone has only weak evidence: the guard says so, and does not say WAIT
    quick = guard.check(conn, now, coin=lost["coin"], side="long")
    assert quick["verdict"] == "GO"
    assert any(m["pattern"] == "revenge" and m["evidence"]["grade"].startswith("weak")
               for m in quick["planned_trade_matches"])


def test_guard_admits_what_it_cannot_know():
    conn, _ = _store()
    conn.execute("MATCH (x:Pattern) REMOVE x.grade")
    now = conn.execute("MATCH (p:Position) RETURN max(p.closed_at) AS t").single()["t"] + 60000
    assert guard.check(conn, now, adding_to_loser=True)["verdict"] == "UNKNOWN"
    empty = graph.open_store(":memory:")
    empty.execute("CREATE (:Wallet {address: 'nobody'})")
    assert guard.check(empty, 1)["verdict"] == "UNKNOWN"


def test_ladder_of_adds_against_the_position_is_a_path():
    conn, pos = _store()
    laddered = [p for p in pos if p.adds_against]
    assert laddered
    rows = conn.execute(
        "MATCH path = (p:Position)-[:AVERAGED_DOWN]->(:Add)-[:THEN]->*(last:Add) "
        "WHERE NOT EXISTS { (last)-[:THEN]->(:Add) } "
        "RETURN p.id AS id, length(path) AS rungs").fetchall()
    assert {r["id"]: r["rungs"] for r in rows} == {p.id: p.adds_against for p in laddered}


def test_averaging_down_is_only_counted_at_a_worse_price():
    fills = [f("ETH", "B", 3000, 1, 0, 0, 1, 1), f("ETH", "B", 3100, 1, 1, 0, 2, 2),   # add at a better price
             f("ETH", "A", 3200, 2, 2, 300, 3, 3),
             f("ETH", "B", 3000, 1, 0, 0, 4, 4), f("ETH", "B", 2900, 1, 1, 0, 5, 5),   # add under water
             f("ETH", "A", 2800, 2, 2, -300, 6, 6)]
    first, second = positions.build_positions("0xabc", fills)
    assert (first.adds, first.adds_against) == (1, 0)
    assert (second.adds, second.adds_against) == (1, 1) and len(second.ladder) == 1


def _cohort(wallets=40):
    """Forty made-up traders with the same planted habits, enough to test evidence."""
    conn = graph.open_store(":memory:")
    for i in range(wallets):
        key = f"w_{i:03d}"
        fills = sample.make_fills(seed=100 + i, days=45)
        pos = positions.build_positions(key, fills)
        graph.write(conn, key, pos, derive_now=False, worst=_worst(fills, pos))
    graph.derive(conn)
    return conn


def test_study_judges_adding_at_the_decision_point_and_audits_itself():
    conn = _cohort()
    rows = {r["pattern"]: r for r in study.evidence(conn, write=False)}
    down = rows["average_down"]
    assert down["grade"].startswith("solid") and down["liq_per_1000"] > 0
    assert "equally far under water" in down["compared"]
    bands = study.under_water(conn)
    assert bands and bands[0]["added_liq_per_1000"] > bands[0]["did_not_liq_per_1000"]
    audit = {r["guard_said"]: r for r in study.audit(conn)["result_on_second_half"]}
    assert audit["WAIT"]["liquidated_per_1000"] > audit["GO"]["liquidated_per_1000"]
    peers = conn.execute("MATCH (:Wallet)-[e:PEER_OF]->(:Wallet) RETURN count(e) AS c").single()["c"]
    assert peers == 40 * 8


def test_worst_move_comes_from_prices_seen_while_the_position_was_open():
    fills = [f("ETH", "B", 3000, 1, 0, 0, 1000, 1), f("ETH", "B", 2940, 1, 1, 0, 2000, 2),
             f("ETH", "A", 2910, 2, 2, -120, 3000, 3)]
    (p,) = positions.build_positions("0xabc", fills)
    assert _worst(fills, [p])[p.id] == 3.0          # 2910 is 3% under the entry at 3000
    assert p.adds_against == 1 and p.ladder[0][3] == 2.0


def test_the_agent_server_answers_and_cannot_write(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("mcp")
    import asyncio
    import json
    import sys
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    path = str(tmp_path / "t.rl")
    conn = graph.open_store(path)
    fills = sample.make_fills()
    graph.write(conn, sample.ADDRESS, positions.build_positions(sample.ADDRESS, fills))
    before = _counts(conn)
    conn.close()

    async def talk():
        params = StdioServerParameters(command=sys.executable, args=["-m", "tiltgraph", "serve", "-d", path])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {t.name for t in (await s.list_tools()).tools}
                check = await s.call_tool("pre_trade_check", {"adding_to_loser": True})
                write = await s.call_tool("read_cypher", {"query": "CREATE (:Hacked)"})
                return names, check.content[0].text, write.content[0].text

    names, check, write = asyncio.run(talk())
    assert {"pre_trade_check", "habits", "peers", "spirals", "read_cypher"} <= names
    assert json.loads(check)["verdict"] in ("GO", "WAIT", "UNKNOWN")
    assert "error" in json.loads(write)
    conn = graph.open_store(path, read_only=True)
    assert _counts(conn) == before

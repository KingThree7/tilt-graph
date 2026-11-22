import relate

from tiltgraph import graph, guard, positions, report, sample

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


def _store():
    conn = graph.open_store(":memory:")
    pos = positions.build_positions(sample.ADDRESS, sample.make_fills())
    graph.write(conn, sample.ADDRESS, pos)
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
    assert spirals and spirals[0]["positions"] >= 3 and spirals[0]["pnl"] < 0


def test_thresholds_can_be_changed_without_new_data():
    conn, pos = _store()
    q = "MATCH (:Position)-[:SHOWS]->(:Pattern {name: 'revenge'}) RETURN count(*) AS c"
    wide = conn.execute(q).single()["c"]
    graph.write(conn, sample.ADDRESS, pos, window=3)
    assert 0 < conn.execute(q).single()["c"] < wide


def test_guard_says_wait_right_after_a_loss_and_unknown_without_cases():
    conn, _ = _store()
    lost = conn.execute("MATCH (p:Position) WHERE p.won = false RETURN p.closed_at AS t "
                        "ORDER BY t DESC LIMIT 1").single()["t"]
    out = guard.check(conn, lost + 8 * 60000)
    assert out["verdict"] == "WAIT" and out["similar_cases"] >= 8
    assert guard.check(conn, lost + 8 * 60000, min_cases=10 ** 6)["verdict"] == "UNKNOWN"
    empty = relate.connect(":memory:")
    assert guard.check(empty, 1)["verdict"] == "NO HISTORY"

import math

import pytest
from hypothesis import given, strategies as st

import arena_helpers as H
from arena.match import MatchConfig, play_match
from evals.score import MatchResult, Standing, leaderboard, score

approx = pytest.approx
INF = math.inf


def check_against(result, expected):
    assert result.names == expected["names"]
    assert result.hands == expected["hands"]
    assert result.matches == expected["matches"]
    assert result.bb_per_100 == approx(expected["bb_per_100"], abs=1e-9)
    for got, want in zip(result.ci95, expected["ci95"]):
        assert got == INF if want == INF else got == approx(want, abs=1e-7)
    assert result.match_bb_per_100 == [approx(r, abs=1e-9)
                                       for r in expected["match_bb_per_100"]]
    assert result.faults == expected["faults"]
    assert result.first_faults == expected["first_faults"]
    assert result.max_move_seconds == approx(expected["max_move_seconds"])


def fold_call_log(first="f", bb=20, deals=2):
    """Heads-up always-fold (f) vs always-call (c)."""
    sb = bb // 2
    hands = []
    seats = [[first, "c" if first == "f" else "f"]]
    seats.append(seats[0][::-1])
    for d in range(deals):
        for k, s in enumerate(seats):
            net = [-sb, sb] if s[0] == "f" else [bb, -bb]
            hands.append((k, d, s, net, {}))
    return H.mk_log(["f", "c"] if first == "f" else ["c", "f"], hands, bb=bb)


def test_SC01_invalid(dummies):
    """SC-01: score raises ValueError for no logs, any tournament log, or logs with different name sets."""
    with pytest.raises(ValueError):
        score([])
    tour = H.mk_log(["f", "c"], [(0, 0, ["f", "c"], [1, -1], {})],
                    mode="tournament")
    with pytest.raises(ValueError):
        score([fold_call_log(), tour])
    other = H.mk_log(["f", "x"], [(0, 0, ["f", "x"], [1, -1], {})])
    with pytest.raises(ValueError):
        score([fold_call_log(), other])


def test_SC02_SC_M11_names_follow_first_log():
    """SC-02/SC-M11: fold vs call scores -75/+75 with ci95 0; results follow the first log's names even when later logs list them differently."""
    result = score([fold_call_log("f"), fold_call_log("c")])
    assert isinstance(result, MatchResult)
    assert result.names == ["f", "c"]
    assert result.bb_per_100 == approx([-75, 75])
    assert result.ci95 == approx([0, 0], abs=1e-9)
    assert result.match_bb_per_100 == [approx([-75, 75])] * 2
    assert (result.hands, result.matches) == (8, 2)
    result = score([fold_call_log("c")])
    assert result.names == ["c", "f"] and result.bb_per_100 == approx(
        [75, -75])


def test_SC_M12_e2e_through_arena(dummies):
    """SC-M12: two folders and a caller played by arena duplicate mode score -50/-50/+100."""
    players = dummies(f1=H.Folder, f2=H.Folder, c=H.Rec)
    log = play_match(players, MatchConfig(deals=2))
    result = score([log])
    assert result.bb_per_100 == approx([-50, -50, 100])


def test_SC03_values_normalise_per_log():
    """SC-03: a value is (sum of a player's net over a deal's hands) / n / that log's big_blind; logs with different blinds and deals combine."""
    a = fold_call_log("f", bb=20)
    b = fold_call_log("f", bb=40, deals=1)
    expected = H.score_oracle([a, b])
    check_against(score([a, b]), expected)
    assert score([a, b]).bb_per_100 == approx([-75, 75])


def test_SC_M13_single_sample_ci_inf():
    """SC-M13: with a single (log, deal) sample ci95 is inf; hands counts HandLogs and matches counts logs."""
    log = H.mk_log(["a", "b"], [(0, 0, ["a", "b"], [10, -10], {}),
                                (1, 0, ["b", "a"], [30, -30], {})])
    result = score([log])
    assert result.ci95 == [INF, INF]
    assert result.bb_per_100 == approx([-50, 50])
    assert (result.hands, result.matches) == (2, 1)


def test_SC04_faults_and_timing():
    """SC-04: faults count HandLogs where the player's seat faulted, first_faults is the first text in log then hand order, max_move_seconds is the max over logs."""
    l1 = H.mk_log(["a", "b"], [(0, 0, ["a", "b"], [0, 0], {1: "b1"}),
                               (1, 0, ["b", "a"], [0, 0], {0: "b2", 1: "a1"})],
                  mms={"a": 0.5, "b": 0.0})
    l2 = H.mk_log(["b", "a"], [(0, 0, ["b", "a"], [0, 0], {0: "b3"})],
                  mms={"a": 0.25, "b": 2.0})
    result = score([l1, l2])
    assert result.faults == [1, 3]
    assert result.first_faults == ["a1", "b1"]
    assert result.max_move_seconds == approx([0.5, 2.0])


def test_LB01_empty_and_tournament_only():
    """LB-01: leaderboard of no logs or only tournament logs is []; tournament logs are skipped."""
    tour = H.mk_log(["a", "b"], [(0, 0, ["a", "b"], [5, -5], {})],
                    mode="tournament")
    assert leaderboard([]) == [] and leaderboard([tour]) == []
    rows = leaderboard([tour, fold_call_log()])
    assert [r.name for r in rows] == ["c", "f"]
    assert rows[0].hands == 4


def test_LB02_LB03_pooled_and_sorted():
    """LB-02/LB-03: players in different subsets of logs are pooled over their own logs, hands = HandLogs they sat in; sorted by bb_per_100 desc, then name."""
    l1 = fold_call_log()
    l2 = H.mk_log(["c", "x"], [(0, 0, ["c", "x"], [-20, 20], {}),
                               (1, 0, ["x", "c"], [20, -20], {}),
                               (0, 1, ["c", "x"], [10, -10], {}),
                               (1, 1, ["x", "c"], [-10, 10], {})])
    l3 = H.mk_log(["y", "z"], [(0, 0, ["y", "z"], [0, 0], {}),
                               (1, 0, ["z", "y"], [0, 0], {})])
    rows = leaderboard([l1, l2, l3])
    assert all(isinstance(r, Standing) for r in rows)
    expected = H.leaderboard_oracle([l1, l2, l3])
    assert [(r.name, r.hands) for r in rows] == [(e[0], e[1])
                                                for e in expected]
    for r, e in zip(rows, expected):
        assert r.bb_per_100 == approx(e[2], abs=1e-9)
        assert r.ci95 == INF if e[3] == INF else r.ci95 == approx(e[3])
    # c and x tie at +25: the name breaks the tie
    assert [r.name for r in rows] == ["c", "x", "y", "z", "f"]


@st.composite
def dup_logs(draw):
    names = ["a", "b", "c", "d"][:draw(st.integers(2, 4))]
    n = len(names)
    logs = []
    for _ in range(draw(st.integers(1, 3))):
        order = list(draw(st.permutations(names)))
        bb = draw(st.sampled_from([2, 10, 20]))
        deals = draw(st.integers(1, 3))
        hands = []
        for k in range(n):
            seats = [order[(i + k) % n] for i in range(n)]
            for d in range(deals):
                net = draw(st.lists(st.integers(-200, 200), min_size=n - 1,
                                    max_size=n - 1))
                net.append(-sum(net))
                faults = draw(st.dictionaries(st.integers(0, n - 1),
                                              st.sampled_from(["x", "y"]),
                                              max_size=1))
                hands.append((k, d, seats, net, faults))
        mms = {nm: draw(st.floats(0, 3)) for nm in names}
        logs.append(H.mk_log(draw(st.permutations(names)), hands, bb=bb,
                             mms=mms, deals=deals))
    return logs


@given(dup_logs())
def test_SCP1_LBP1_against_oracle(logs):
    """SC-P1/LB-P1: score and leaderboard equal an independent oracle; bb_per_100 and each match row sum to 0."""
    result = score(logs)
    check_against(result, H.score_oracle(logs))
    assert sum(result.bb_per_100) == approx(0, abs=1e-9)
    for row in result.match_bb_per_100:
        assert sum(row) == approx(0, abs=1e-9)
    rows = leaderboard(logs)
    expected = H.leaderboard_oracle(logs)
    assert sorted(r.name for r in rows) == sorted(e[0] for e in expected)
    for a, b in zip(rows, rows[1:]):
        assert a.bb_per_100 >= b.bb_per_100 - 1e-9
        if abs(a.bb_per_100 - b.bb_per_100) < 1e-12:
            assert a.name < b.name
    for r in rows:
        e = next(x for x in expected if x[0] == r.name)
        assert r.hands == e[1]
        assert r.bb_per_100 == approx(e[2], abs=1e-9)


@given(st.lists(dup_logs(), min_size=1, max_size=1))
def test_AP4_store_then_score(tmp_path_factory, wrapped):
    """AP-4: scoring logs read back from a ResultStore gives the same results as scoring them in memory."""
    from arena.results import ResultStore

    logs = wrapped[0]
    store = ResultStore(str(tmp_path_factory.mktemp("ap4") / "r.jsonl"))
    for log in logs:
        store.add(log)
    assert score(store.logs()) == score(logs)
    assert leaderboard(store.logs()) == leaderboard(logs)

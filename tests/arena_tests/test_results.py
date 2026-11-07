import json
import os

import pytest
from hypothesis import given, strategies as st

import arena_helpers as H
from arena.match import MatchConfig
from arena.results import ResultStore, load_bankroll, save_bankroll


def sample_logs():
    dup = H.mk_log(["a", "b"], [(0, 0, ["a", "b"], [-10, 10], {}),
                                (1, 0, ["b", "a"], [20, -20], {1: "boom"})],
                   mms={"a": 0.25, "b": 0.0}, seed=3,
                   launcher=("bwrap", "--x"), timeout=2.5, memory_mb=256)
    tour = H.mk_log(["a", "b", "c"],
                    [(0, 0, ["a", "b", "c"], [5, -5, 0], {2: "x"}),
                     (0, 1, ["a", "b"], [100, -100], {})],
                    mode="tournament", final_stacks={"a": 1, "b": 0, "c": 2},
                    checkpoints={"a": "ck/a.pkl"},
                    stacks={"a": 1, "b": 2, "c": 3}, checkpoint_dir=None)
    return [dup, tour]


def test_RS01_lazy_and_empty(tmp_path):
    """RS-01: constructing a ResultStore touches nothing; logs() of a missing file is []."""
    path = tmp_path / "sub" / "results.jsonl"
    store = ResultStore(str(path))
    assert not (tmp_path / "sub").exists()
    assert store.logs() == []


def test_RS02_round_trip_and_append(tmp_path):
    """RS-02: add appends one JSON line per log (creating folders); logs() returns equal MatchLogs, including config tuples/dicts, HandLogs and int-keyed faults; a second store on the same file keeps earlier lines."""
    path = tmp_path / "a" / "b" / "results.jsonl"
    store = ResultStore(str(path))
    logs = sample_logs()
    for log in logs:
        store.add(log)
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(line) for line in lines)
    assert store.logs() == logs
    other = ResultStore(str(path))
    other.add(logs[0])
    assert other.logs() == logs + [logs[0]]
    got = other.logs()[0]
    assert list(got.hands[1].faults) == [1]
    assert got.config.launcher == ("bwrap", "--x")


def test_RS03_malformed_line(tmp_path):
    """RS-03: a malformed line raises ValueError naming its line number."""
    path = tmp_path / "r.jsonl"
    store = ResultStore(str(path))
    for log in sample_logs():
        store.add(log)
    with open(path, "a") as f:
        f.write("{not json\n")
    with pytest.raises(ValueError, match="3"):
        store.logs()


def test_RS04_load_bankroll(tmp_path):
    """RS-04: load_bankroll is {} for a missing file and loads name -> int (0 allowed)."""
    assert load_bankroll(str(tmp_path / "none.json")) == {}
    path = tmp_path / "bank.json"
    path.write_text(json.dumps({"a": 10, "b": 0}))
    assert load_bankroll(str(path)) == {"a": 10, "b": 0}


@pytest.mark.parametrize("content", [
    "[1, 2]", '{"a": "1"}', '{"a": 1.5}', '{"a": true}', '{"a": -1}',
    "not json", '"text"', "null",
])
def test_RS04_load_bankroll_invalid(tmp_path, content):
    """RS-04: load_bankroll raises ValueError unless the file is a JSON object of str -> non-negative int (booleans rejected)."""
    path = tmp_path / "bank.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_bankroll(str(path))


def test_RS05_save_bankroll(tmp_path):
    """RS-05: save_bankroll creates the folder, round-trips, overwrites and leaves no temp files."""
    path = tmp_path / "x" / "y" / "bank.json"
    save_bankroll(str(path), {"a": 5, "b": 0})
    assert load_bankroll(str(path)) == {"a": 5, "b": 0}
    save_bankroll(str(path), {"c": 7})
    assert load_bankroll(str(path)) == {"c": 7}
    assert os.listdir(path.parent) == ["bank.json"]


names_st = st.lists(st.sampled_from(["a", "b", "c", "d"]), min_size=2,
                    max_size=4, unique=True)


@st.composite
def logs_st(draw):
    names = draw(names_st)
    mode = draw(st.sampled_from(["duplicate", "tournament"]))
    hands = []
    for _ in range(draw(st.integers(0, 4))):
        seats = draw(st.permutations(names))
        net = draw(st.lists(st.integers(-500, 500), min_size=len(seats),
                            max_size=len(seats)))
        faults = draw(st.dictionaries(st.integers(0, len(seats) - 1),
                                      st.text(max_size=8), max_size=2))
        hands.append((draw(st.integers(0, 3)), draw(st.integers(0, 5)),
                      list(seats), net, faults))
    mms = {nm: draw(st.floats(0, 10, allow_nan=False)) for nm in names}
    extra = {}
    if mode == "tournament":
        extra = dict(final_stacks={nm: draw(st.integers(0, 999))
                                   for nm in names},
                     checkpoints={names[0]: f"ck/{names[0]}.pkl"},
                     stacks=draw(st.one_of(st.none(), st.just(
                         {nm: 100 for nm in names}))))
    return H.mk_log(names, hands, mode=mode, mms=mms, deals=3,
                    seed=draw(st.integers(0, 10**6)), **extra)


@given(st.lists(logs_st(), max_size=4))
def test_RSP1_round_trip_property(tmp_path_factory, logs):
    """RS-P1: any MatchLogs added to a ResultStore come back equal and in order."""
    path = tmp_path_factory.mktemp("rs") / "r.jsonl"
    store = ResultStore(str(path))
    for log in logs:
        store.add(log)
    assert store.logs() == logs

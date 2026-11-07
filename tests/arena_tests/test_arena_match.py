"""Duplicate mode (migrated from the old evals play_match tests: AM-Mxx)."""

import dataclasses

import pytest
from hypothesis import given, strategies as st

import arena_helpers as H
from arena.match import MatchConfig, MatchLog, play_match, repeat_match
from arena.players import Player
from player_action import PlayerAction as A

approx = pytest.approx


def cfg(**kw):
    kw.setdefault("deals", 1)
    return MatchConfig(**kw)


def by_name(name):
    return [m for m in H.BUILT if m.NAME == name]


# -- validation ----------------------------------------------------------------

def _invalid():
    R = H.Rec
    return {
        "one player": (dict(a=R), {}),
        "ten players": ({f"p{i}": R for i in range(10)}, {}),
        "deals 0": (dict(a=R, b=R), dict(deals=0)),
        "sb 0": (dict(a=R, b=R), dict(small_blind=0)),
        "sb > bb": (dict(a=R, b=R), dict(small_blind=30, big_blind=20)),
        "stack < bb": (dict(a=R, b=R), dict(stack=19)),
        "stacks in duplicate": (dict(a=R, b=R), dict(stacks={"a": 1, "b": 1})),
        "tour stack 0": (dict(a=R, b=R), dict(mode="tournament", stack=0,
                                              checkpoint_dir=None)),
        "tour stacks missing": (dict(a=R, b=R), dict(
            mode="tournament", stacks={"a": 100}, checkpoint_dir=None)),
        "tour stacks extra": (dict(a=R, b=R), dict(
            mode="tournament", stacks={"a": 100, "b": 100, "c": 5},
            checkpoint_dir=None)),
        "tour stacks zero": (dict(a=R, b=R), dict(
            mode="tournament", stacks={"a": 100, "b": 0},
            checkpoint_dir=None)),
    }


@pytest.mark.parametrize("case", list(_invalid()))
def test_AM_M01_invalid_config(case, dummies):
    """AM-M01: invalid player counts, deals, blinds, stacks (duplicate stack < big blind, stacks given in duplicate, tournament stacks < 1 or with the wrong names) raise ValueError before any model is built."""
    bases, kw = _invalid()[case]
    players = dummies(**bases)
    with pytest.raises(ValueError):
        play_match(players, cfg(**kw))
    assert H.BUILT == [] and H.LOG == []


@pytest.mark.parametrize("names", [["a", "a"], ["", "b"], ["a/b", "c"],
                                   ["a\\b", "c"], [".", "b"], ["..", "b"],
                                   [7, "b"]])
def test_AM_M01_invalid_names(names, dummies):
    """AM-M01: duplicate, empty, non-str names or names with "/", "\\", "." or ".." raise ValueError before any model is built."""
    dummies(ok=H.Rec)
    players = [Player(nm, "dummy:ok") for nm in names]
    with pytest.raises(ValueError):
        play_match(players, cfg())
    assert H.BUILT == []


@pytest.mark.parametrize("n", [2, 9])
def test_AM_M01_sizes_and_stack_equal_bb_accepted(n, dummies):
    """AM-M01: 2 and 9 players, and stack == big_blind, are accepted."""
    players = dummies(**{f"p{i}": H.Rec for i in range(n)})
    log = play_match(players, cfg(stack=20))
    assert len(log.hands) == n


# -- schedule ------------------------------------------------------------------

@pytest.mark.parametrize("seed", [0, 5])
def test_AM_M05_seating(seed, dummies):
    """AM-M05: heads-up keeps the given order; with 3+ players the order is the names shuffled by Random(f"{seed}:seats"); seat i in rotation k is order[(i + k) % n]."""
    for bases in (dict(b=H.Rec, a=H.Rec),
                  dict(c=H.Rec, a=H.Rec, d=H.Rec, b=H.Rec)):
        players = dummies(**bases)
        log = play_match(players, cfg(deals=2, seed=seed))
        names = list(bases)
        order = H.seat_order(names, seed)
        n = len(names)
        assert [(h.rotation, h.deal) for h in log.hands] == [
            (k, d) for k in range(n) for d in range(2)]
        for h in log.hands:
            assert h.seats == [order[(i + h.rotation) % n] for i in range(n)]


def test_AM_M06_decks_fresh_cards(dummies):
    """AM-M06: deal d uses full_deck() shuffled by Random(f"{seed}:{d}"), the same cards in every rotation but new Card objects each hand."""
    players = dummies(a=H.Rec, b=H.Rec, c=H.Rec)
    log = play_match(players, cfg(deals=2, seed=9))
    order = H.seat_order(["a", "b", "c"], 9)
    seen = {}
    for m in H.BUILT:
        rotation = m.index
        for d, (hand, pid) in enumerate(m.seeds):
            deck = H.deck_dup(9, d)
            assert hand == deck[2 * pid:2 * pid + 2]
            assert order[(pid + rotation) % 3] == m.NAME
            seen.setdefault((d, pid), []).append(hand)
    for hands in seen.values():
        assert len(hands) == 3
        assert hands[0][0] is not hands[1][0] is not hands[2][0]
        assert hands[0][0] is not hands[2][0]


def test_AM_M04_fresh_models_per_rotation(dummies):
    """AM-M04: one build per player per rotation; each model plays exactly `deals` hands, always in the same seat."""
    players = dummies(a=H.Rec, b=H.Rec, c=H.Rec)
    play_match(players, cfg(deals=3))
    for nm in "abc":
        models = by_name(nm)
        assert len(models) == 3
        for m in models:
            assert len(m.seeds) == 3 and len({p for _, p in m.seeds}) == 1


def test_AM01_dummy_seed_stable(dummies):
    """AM-01: each dummy is built with the same int seed in every rotation, and again in an identical match."""
    players = dummies(a=H.Rec, b=H.Rec, c=H.Rec)
    play_match(players, cfg(deals=1, seed=4))
    first = {nm: {m.ctor_seed for m in by_name(nm)} for nm in "abc"}
    assert all(len(s) == 1 and isinstance(next(iter(s)), int)
               for s in first.values())
    players = dummies(a=H.Rec, b=H.Rec, c=H.Rec)
    play_match(players, cfg(deals=1, seed=4))
    assert first == {nm: {m.ctor_seed for m in by_name(nm)} for nm in "abc"}


def test_AM_M07_hand_setup(dummies):
    """AM-M07: every hand uses button 0, the configured blinds, [stack] * n and teams = the seated names."""
    players = dummies(x=H.Rec, y=H.Rec, z=H.Rec)
    log = play_match(players, cfg(deals=2, stack=300, small_blind=5,
                                  big_blind=10))
    assert all(m.runs for m in H.BUILT)
    seats_by_rotation = {h.rotation: tuple(h.seats) for h in log.hands}
    for m in H.BUILT:
        for run in m.runs:
            assert run["teams"] == seats_by_rotation[m.index]
            assert run["button"] == 0 and (run["sb"], run["bb"]) == (5, 10)
            assert sum(run["stacks"]) + run["pot"] == 900
            assert run["view"] == "GameStateView"


def test_AM02_handlogs_match_direct_play(dummies):
    """AM-02: HandLogs are in play order and equal direct play_hand results (rotation, deal, seats, net, faults); final_stacks None, checkpoints {}."""
    bases = dict(p=H.CardBot, q=H.SometimesFaults, r=H.Oddball, s=H.Rec)
    players = dummies(**bases)
    log = play_match(players, cfg(deals=3, seed=3))
    assert H.hand_tuples(log) == [
        (r, d, s, n, f) for r, d, s, n, f in H.oracle_duplicate(bases, 3, 3)]
    assert log.final_stacks is None and log.checkpoints == {}


def test_AM_M08_close_between_rotations(dummies):
    """AM-M08: every model with close is closed once, after its rotation and before the next rotation's first build; models without close work."""
    players = dummies(a=H.Rec, b=H.Bare, c=H.SeedRaiser)
    play_match(players, cfg(deals=2))
    for m in H.BUILT:
        if hasattr(m, "closed"):
            assert m.closed == 1
    for k in range(3):
        closes = [i for i, e in enumerate(H.LOG)
                  if e[0] == "close" and e[2] == k]
        seeds = [i for i, e in enumerate(H.LOG) if e[0] == "seed"
                 and e[2] == k]
        builds = [i for i, e in enumerate(H.LOG)
                  if e[0] == "build" and e[2] == k + 1]
        assert len(closes) == 2
        assert min(closes) > max(seeds)
        if builds:
            assert max(closes) < min(builds)


def test_AM_M21_ctor_error_closes_built(dummies):
    """AM-M21: a model build that raises propagates after closing every model already built in that rotation."""
    players = dummies(a=H.Rec, b=H.CtorRaiser, c=H.LateCloseRaiser)
    with pytest.raises(H.CtorError):
        play_match(players, cfg())
    assert all(m.closed == 1 for m in H.BUILT)


def test_AM_M22_close_errors(dummies):
    """AM-M22: a close that raises propagates after the rest are closed; when several raise, every close is attempted."""
    players = dummies(a=H.CloseRaiser, b=H.Rec, c=H.Rec)
    with pytest.raises(H.CloseError):
        play_match(players, cfg())
    assert all(m.closed == 1 for m in H.BUILT) and len(H.BUILT) == 3
    players = dummies(a=H.CloseRaiser, b=H.CloseRaiser, c=H.Rec)
    with pytest.raises(H.CloseError):
        play_match(players, cfg())
    assert all(m.closed == 1 for m in H.BUILT)


def test_AM_M23_propagating_error_wins(dummies):
    """AM-M23: a BaseException from play, or a build error, already propagating wins over close errors; every model is still closed."""
    players = dummies(a=H.Booms, b=H.CloseRaiser, c=H.Rec)
    with pytest.raises(H.Boom):
        play_match(players, cfg())
    assert all(m.closed == 1 for m in H.BUILT)
    players = dummies(a=H.BoomCloseRaiser, b=H.Rec)
    with pytest.raises(H.Boom):
        play_match(players, cfg())
    players = dummies(a=H.LateCloseRaiser, b=H.CtorRaiser)
    with pytest.raises(H.CtorError):
        play_match(players, cfg())
    assert all(m.closed == 1 for m in H.BUILT)


def test_AM_M15_M16_M18_faults(dummies):
    """AM-M15/M16/M18: a seed raiser faults in every hand, is closed and has max_move_seconds 0.0; a run raiser faults whenever asked; faults never stop the match."""
    players = dummies(s=H.SeedRaiser, r=H.RunRaiser, c=H.Rec)
    log = play_match(players, cfg(deals=2))
    assert len(log.hands) == 6
    for h in log.hands:
        assert h.seats.index("s") in h.faults
        assert h.seats.index("c") not in h.faults
    assert any(h.seats.index("r") in h.faults for h in log.hands)
    assert log.max_move_seconds["s"] == 0.0
    assert all(m.closed == 1 for m in by_name("s"))


def test_AM_M19_timing(dummies):
    """AM-M19: max_move_seconds per name is the slowest run in the match, counting runs that raise; 0.0 for a player never asked."""
    players = dummies(slow=H.Sleeper, raiser=H.SlowRaiser, fast=H.Rec,
                      never=H.SeedRaiser)
    log = play_match(players, cfg())
    mms = log.max_move_seconds
    assert set(mms) == {"slow", "raiser", "fast", "never"}
    assert 0.03 <= mms["slow"] < 2 and 0.03 <= mms["raiser"] < 2
    assert 0 <= mms["fast"] < mms["slow"] and mms["never"] == 0.0


def test_AM03_end_game_through_proxy(dummies):
    """AM-03: a model with end_game is told after every hand (lists end with a win); one without end_game plays; an end_game error is a fault starting with "end_game"."""
    players = dummies(told=H.EndRec, plain=H.Rec, bad=H.EndRaiser)
    log = play_match(players, cfg(deals=2))
    for m in by_name("told"):
        assert len(m.ends) == 2
        assert all(lst[-1].kind == "win" for lst in m.ends)
    for h in log.hands:
        i = h.seats.index("bad")
        assert h.faults[i].startswith("end_game")
        assert h.seats.index("plain") not in h.faults


def test_AM04_log_shape(dummies):
    """AM-04: MatchLog.names is the given order, config is echoed, and duplicate mode never checkpoints."""
    players = dummies(zed=H.Ckpt, alpha=H.Rec, mid=H.Rec)
    config = cfg(deals=2, seed=11, checkpoint_dir="should-not-be-used")
    log = play_match(players, config)
    assert isinstance(log, MatchLog)
    assert log.names == ["zed", "alpha", "mid"]
    assert log.config == config
    assert log.checkpoints == {} and log.final_stacks is None
    assert all(m.checkpoints == 0 for m in H.BUILT)


def test_AM_M20_proxy_transparent(dummies):
    """AM-M20: the timing proxy changes nothing the engine sees: odd returns and raisers give the same HandLogs as direct play."""
    bases = dict(o=H.Oddball, r=H.RunRaiser, s=H.SeedRaiser, c=H.CardBot)
    players = dummies(**bases)
    log = play_match(players, cfg(deals=2, seed=5))
    assert H.hand_tuples(log) == [tuple(x) for x in
                                  H.oracle_duplicate(bases, 2, 5)]


def test_AM05_remote_and_dummy_mix(bots_dir, dummies):
    """AM-05: a duplicate match mixing pickled teams and a dummy completes, faults the raising team, and leaves no worker running."""
    from arena.players import player

    dummies(dum=H.Rec)
    before = H.read_pids(bots_dir)
    players = [player(str(bots_dir / "Normal.pkl")),
               player(str(bots_dir / "RunRaise.pkl")), player("dum")]
    log = play_match(players, cfg(deals=1, timeout=10))
    assert log.names == ["Normal", "RunRaise", "dum"]
    assert len(log.hands) == 3
    assert all(h.seats.index("RunRaise") in h.faults for h in log.hands)
    new = H.read_pids(bots_dir)[len(before):]
    assert len(new) == 6  # 2 teams x 3 rotations
    assert all(H.wait_dead(pid, 2.5) for pid in new)


def test_RP01_repeat_match(dummies):
    """RP-01: repeat_match plays `times` matches with seeds seed, seed+1, ...; times < 1 raises ValueError before building."""
    players = dummies(a=H.Rec, b=H.Rec)
    logs = repeat_match(players, cfg(seed=7), 3)
    assert [log.config.seed for log in logs] == [7, 8, 9]
    assert all(log.config == dataclasses.replace(cfg(seed=7), seed=7 + i)
               for i, log in enumerate(logs))
    players = dummies(a=H.Rec, b=H.Rec)
    with pytest.raises(ValueError):
        repeat_match(players, cfg(), 0)
    assert H.BUILT == []


# -- properties ----------------------------------------------------------------

KINDS = [H.CardBot, H.SometimesFaults, H.Folder, H.Rec, H.RunRaiser,
         H.Shover]


@st.composite
def dup_args(draw):
    n = draw(st.integers(2, 4))
    return dict(bases={f"m{i}": draw(st.sampled_from(KINDS))
                       for i in range(n)},
                deals=draw(st.integers(1, 2)), seed=draw(st.integers(0, 999)))


@given(dup_args())
def test_AP1_AP2_duplicate_properties(args):
    """AP-1/AP-2: every HandLog nets to 0; each player sits in each seat once per deal; the match is deterministic apart from max_move_seconds."""
    import arena.dummies

    old = arena.dummies.DUMMIES
    try:
        logs = []
        for _ in range(2):
            H.reset()
            arena.dummies.DUMMIES = {nm: H.named(nm, b)
                                     for nm, b in args["bases"].items()}
            players = [Player(nm, "dummy:" + nm) for nm in args["bases"]]
            logs.append(play_match(players, cfg(deals=args["deals"],
                                                seed=args["seed"])))
    finally:
        arena.dummies.DUMMIES = old
    log = logs[0]
    n = len(args["bases"])
    assert len(log.hands) == n * args["deals"]
    for h in log.hands:
        assert sum(h.net) == 0
    for d in range(args["deals"]):
        for nm in args["bases"]:
            seats = sorted(h.seats.index(nm) for h in log.hands
                           if h.deal == d)
            assert seats == list(range(n))
    logs[0].max_move_seconds = logs[1].max_move_seconds = None
    assert logs[0] == logs[1]

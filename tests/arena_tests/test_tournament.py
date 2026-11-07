import os
import pickle

import pytest
from hypothesis import given, strategies as st

import arena_helpers as H
from arena.match import MatchConfig, play_match
from arena.players import Player, player

BUST = dict(a=H.Shover, b=H.CardBot, c=H.Rec, d=H.Shover)
BUST_STACKS = {"a": 60, "b": 200, "c": 40, "d": 100}


def tcfg(**kw):
    kw.setdefault("mode", "tournament")
    kw.setdefault("checkpoint_dir", None)
    return MatchConfig(**kw)


def by_name(name):
    return [m for m in H.BUILT if m.NAME == name]


def test_AT01_models_built_once(dummies):
    """AT-01: one model per player for the whole match, built once in the given order and closed once at the end (busted players' models included)."""
    players = dummies(**BUST)
    play_match(players, tcfg(deals=30, stacks=BUST_STACKS))
    assert [m.NAME for m in H.BUILT] == ["a", "b", "c", "d"]
    assert all(m.closed == 1 for m in H.BUILT)
    last_seed = max(i for i, e in enumerate(H.LOG) if e[0] == "seed")
    assert min(i for i, e in enumerate(H.LOG) if e[0] == "close") > last_seed


def test_AT02_AT07_matches_direct_play(dummies):
    """AT-02/AT-03/AT-07: stacks from config.stacks, seats in the given order, decks Random(f"{seed}:t:{h}") and the moving button give HandLogs and final_stacks equal to direct play; chips are conserved."""
    players = dummies(**BUST)
    log = play_match(players, tcfg(deals=30, stacks=BUST_STACKS, seed=0))
    hands, chips, buttons = H.oracle_tournament(BUST, 30, seed=0,
                                                stacks=BUST_STACKS)
    assert H.hand_tuples(log) == [tuple(h) for h in hands]
    assert log.final_stacks == chips
    assert sum(log.final_stacks.values()) == sum(BUST_STACKS.values())
    for nm in BUST:
        nets = sum(h.net[h.seats.index(nm)] for h in log.hands
                   if nm in h.seats)
        assert log.final_stacks[nm] == BUST_STACKS[nm] + nets
    assert log.names == ["a", "b", "c", "d"]
    assert all(h.rotation == 0 for h in log.hands)


def test_AT04_busted_never_seated(dummies):
    """AT-04: a busted player is never seated or seeded again."""
    players = dummies(**BUST)
    log = play_match(players, tcfg(deals=30, stacks=BUST_STACKS, seed=0))
    for m in H.BUILT:
        seated = [h for h in log.hands if m.NAME in h.seats]
        assert len(m.seeds) == len(seated)
        if log.final_stacks[m.NAME] == 0:
            last = max(h.deal for h in seated)
            assert all(m.NAME not in h.seats for h in log.hands
                       if h.deal > last)


def test_AT05_button_moves_and_skips_busted(dummies):
    """AT-05: the first button is the first seated player; each later button is the next seated player after the previous button player in seat order, also when that player just busted."""
    players = dummies(**BUST)
    log = play_match(players, tcfg(deals=30, stacks=BUST_STACKS, seed=0))
    _, _, buttons = H.oracle_tournament(BUST, 30, seed=0, stacks=BUST_STACKS)
    assert buttons[:3] == ["a", "b", "d"]  # a and c bust in hand 0
    checked = 0
    for m in H.BUILT:
        mine = [h for h in log.hands if m.NAME in h.seats]
        for run in m.runs:
            deal = mine[run["hand"]].deal
            assert run["teams"][run["button"]] == buttons[deal]
            checked += 1
    assert checked > 0


def test_AT05b_button_sequence_without_busts(dummies):
    """AT-05: with nobody busting, the button rotates a, b, c, a, ... (one hand each)."""
    players = dummies(a=H.Folder, b=H.Folder, c=H.Folder)
    log = play_match(players, tcfg(deals=5))
    # the button folds or wins blinds: with all-fold the big blind wins,
    # so each hand's nets identify the blinds and hence the button
    order = ["a", "b", "c"]
    for h, expected in zip(log.hands, ["a", "b", "c", "a", "b"]):
        b = order.index(expected)
        sb, bb = order[(b + 1) % 3], order[(b + 2) % 3]
        net = dict(zip(h.seats, h.net))
        assert net == {expected: 0, sb: -10, bb: 10}


def test_AT06_exact_deals_and_early_stop(dummies):
    """AT-06: exactly `deals` hands when nobody busts; the match stops early once fewer than 2 players have chips."""
    players = dummies(a=H.Rec, b=H.Rec, c=H.Rec)
    log = play_match(players, tcfg(deals=7, stack=1000))
    assert len(log.hands) == 7
    players = dummies(**BUST)
    log = play_match(players, tcfg(deals=30, stacks=BUST_STACKS, seed=0))
    assert len(log.hands) == 5
    assert sorted(log.final_stacks.values()) == [0, 0, 0, 400]


def test_AT08_checkpoints_of_teams(bots_dir, dummies, tmp_path):
    """AT-08: at the end every team's checkpoint is written to <dir>/<name>.pkl (folder created, old file replaced, no temp files), recorded in checkpoints, and unpickles to the end state."""
    dummies(dum=H.Shover)
    out = tmp_path / "deep" / "ckpt"
    out.mkdir(parents=True)
    (out / "Normal.pkl").write_bytes(b"old")
    players = [player(str(bots_dir / "Normal.pkl")),
               player(str(bots_dir / "NoEnd.pkl")), player("dum")]
    log = play_match(players, tcfg(deals=3, stack=100,
                                   checkpoint_dir=str(out), timeout=10))
    expected = {nm: os.path.join(str(out), nm + ".pkl")
                for nm in ("Normal", "NoEnd")}
    assert log.checkpoints == expected
    assert sorted(os.listdir(out)) == ["NoEnd.pkl", "Normal.pkl"]
    model = pickle.loads((out / "Normal.pkl").read_bytes())
    seated = sum(1 for h in log.hands if "Normal" in h.seats)
    assert model.seeds == seated and model.ends == seated
    fresh = tmp_path / "new" / "dir"
    log = play_match(players, tcfg(deals=1, checkpoint_dir=str(fresh),
                                   timeout=10))
    assert sorted(os.listdir(fresh)) == ["NoEnd.pkl", "Normal.pkl"]


def test_AT09_checkpoint_rules(dummies, tmp_path):
    """AT-09: models with a callable checkpoint are saved (busted ones too); those without are skipped; a raising checkpoint is skipped; checkpoint_dir None writes nothing."""
    players = dummies(a=H.ShoverCkpt, b=H.Rec, c=H.CkptRaiser, d=H.Ckpt)
    out = tmp_path / "ck"
    log = play_match(players, tcfg(deals=20, stacks={"a": 1000, "b": 1000,
                                                     "c": 1000, "d": 30},
                                   checkpoint_dir=str(out), seed=1))
    assert log.final_stacks["d"] == 0  # d busts but is still saved
    assert set(log.checkpoints) == {"a", "d"}
    assert sorted(os.listdir(out)) == ["a.pkl", "d.pkl"]
    for nm in ("a", "d"):
        (m,) = by_name(nm)
        assert (out / f"{nm}.pkl").read_bytes() == (
            f"ckpt-{nm}-{len(m.seeds)}".encode())
    players = dummies(a=H.Ckpt, b=H.Ckpt)
    log = play_match(players, tcfg(deals=2, checkpoint_dir=None))
    assert log.checkpoints == {}
    assert all(m.checkpoints == 0 for m in H.BUILT)


def test_AT10_checkpoint_before_close_and_errors(dummies, tmp_path):
    """AT-10: checkpoints are taken before models are closed; a BaseException from checkpoint propagates after every model is closed; close errors follow the duplicate rules."""
    players = dummies(a=H.Ckpt, b=H.Rec)
    play_match(players, tcfg(deals=2, checkpoint_dir=str(tmp_path / "x")))
    ck = [i for i, e in enumerate(H.LOG) if e[0] == "checkpoint"]
    cl = [i for i, e in enumerate(H.LOG) if e[0] == "close"]
    assert ck and max(ck) < min(cl)
    players = dummies(a=H.CkptBoom, b=H.Rec)
    with pytest.raises(H.Boom):
        play_match(players, tcfg(deals=2, checkpoint_dir=str(tmp_path / "y")))
    assert all(m.closed == 1 for m in H.BUILT)
    players = dummies(a=H.CloseRaiser, b=H.CloseRaiser)
    with pytest.raises(H.CloseError):
        play_match(players, tcfg(deals=2))
    assert all(m.closed == 1 for m in H.BUILT)


def test_AT11_checkpoint_reused_as_team(bots_dir, dummies, tmp_path):
    """AT-11: a checkpoint file is a valid team pickle: a new match plays it with its learned state carried over."""
    dummies(dum=H.Rec)
    out = tmp_path / "ck"
    team = player(str(bots_dir / "Normal.pkl"))
    log1 = play_match([team, player("dum")],
                      tcfg(deals=2, checkpoint_dir=str(out), timeout=10))
    # the team's module ships next to its pickle (one folder per team)
    import shutil
    shutil.copy(bots_dir / "arena_hidden_bots.py", out)
    again = player(log1.checkpoints["Normal"])
    out2 = tmp_path / "ck2"
    log2 = play_match([again, player("dum")],
                      tcfg(deals=3, checkpoint_dir=str(out2), timeout=10))
    model = pickle.loads((out2 / "Normal.pkl").read_bytes())
    assert model.seeds == len(log1.hands) + len(log2.hands)


KINDS = [H.CardBot, H.Shover, H.Rec, H.Folder, H.RunRaiser]


@st.composite
def tour_args(draw):
    n = draw(st.integers(2, 4))
    names = [f"t{i}" for i in range(n)]
    return dict(bases={nm: draw(st.sampled_from(KINDS)) for nm in names},
                stacks={nm: draw(st.integers(1, 300)) for nm in names},
                deals=draw(st.integers(1, 12)), seed=draw(st.integers(0, 99)))


@given(tour_args())
def test_AP3_tournament_properties(args):
    """AP-3: tournaments conserve chips, never seat a player with 0 chips, play at most `deals` hands, and match direct play."""
    import arena.dummies

    old = arena.dummies.DUMMIES
    try:
        H.reset()
        arena.dummies.DUMMIES = {nm: H.named(nm, b)
                                 for nm, b in args["bases"].items()}
        players = [Player(nm, "dummy:" + nm) for nm in args["bases"]]
        log = play_match(players, tcfg(deals=args["deals"],
                                       stacks=args["stacks"],
                                       seed=args["seed"]))
    finally:
        arena.dummies.DUMMIES = old
    assert sum(log.final_stacks.values()) == sum(args["stacks"].values())
    assert len(log.hands) <= args["deals"]
    hands, chips, _ = H.oracle_tournament(args["bases"], args["deals"],
                                          seed=args["seed"],
                                          stacks=args["stacks"])
    assert H.hand_tuples(log) == [tuple(h) for h in hands]
    assert log.final_stacks == chips

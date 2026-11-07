import os

import pytest

import arena_helpers as H
from arena.players import Player, build, player
from arena.remote import RemoteError, RemoteModel


def test_PL01_pickle_spec(dummies):
    """PL-01: a spec ending in ".pkl" is Player(file stem, spec as given), checked before dummy names."""
    dummies(x=H.Rec)
    assert player("bots/team_a.pkl") == Player("team_a", "bots/team_a.pkl")
    assert player("/abs/dir/b.pkl") == Player("b", "/abs/dir/b.pkl")
    assert player("x.y.pkl") == Player("x.y", "x.y.pkl")
    dummies(**{"x.pkl": H.Rec})
    assert player("x.pkl") == Player("x", "x.pkl")


def test_PL02_dummy_spec(dummies):
    """PL-02: a name in arena.dummies.DUMMIES (looked up at call time) becomes Player(name, "dummy:" + name)."""
    dummies(tester=H.Rec)
    assert player("tester") == Player("tester", "dummy:tester")


@pytest.mark.parametrize("spec", ["nobody", "team.txt", "", "TEAM.PKL"])
def test_PL03_unknown_spec(spec, dummies):
    """PL-03: any other spec (unknown name, other extension, upper-case .PKL) raises ValueError."""
    dummies(tester=H.Rec)
    with pytest.raises(ValueError):
        player(spec)


def test_PL04_build_dummy(dummies):
    """PL-04: build of a dummy player returns DUMMIES[name](seed=seed), a new object each call."""
    (p,) = dummies(tester=H.Rec)
    a = build(p, seed=1234, timeout=5)
    b = build(p, seed=1234, timeout=5)
    assert a is not b and a.ctor_seed == 1234 and a.NAME == "tester"


def test_PL05_build_pickle_is_lazy(tmp_path):
    """PL-05: build of a pickle player returns a RemoteModel and starts nothing; a missing file only fails on the first call (RemoteError)."""
    m = build(Player("ghost", str(tmp_path / "nope.pkl")), seed=0,
              timeout=5)
    try:
        assert isinstance(m, RemoteModel)
        with pytest.raises(RemoteError):
            m.seed([], 0)
    finally:
        m.close()


@pytest.mark.parametrize("source", ["weird:x", "dummy:missing", "x"])
def test_PL06_unknown_source(source, dummies):
    """PL-06: build with an unknown source raises ValueError."""
    dummies(tester=H.Rec)
    with pytest.raises(ValueError):
        build(Player("x", source), seed=0, timeout=5)


def test_PL07_relative_path_resolved_at_build(bots_dir, monkeypatch):
    """PL-07: a relative .pkl path is resolved when the model is built, so changing directory afterwards does not matter."""
    from card_utils import Card

    monkeypatch.chdir(bots_dir.parent)
    m = build(player(f"{bots_dir.name}/Normal.pkl"), seed=0, timeout=10)
    monkeypatch.chdir("/")
    try:
        m.seed([Card.parse("AS"), Card.parse("AH")], 0)
        assert isinstance(m.checkpoint(), bytes)
    finally:
        m.close()

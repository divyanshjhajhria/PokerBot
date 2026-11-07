import os
from dataclasses import dataclass

from arena import dummies
from arena.remote import RemoteModel

DUMMY = "dummy:"  # source prefix of a dummy bot


@dataclass(frozen=True)
class Player:
    """Who sits down: a team's pickle or a dummy bot."""

    name: str  # team name: the pickle's file stem, or the dummy's name
    source: str  # path to the .pkl file, or "dummy:<name>"


def player(spec: str) -> Player:
    """
    A player from a command-line style spec: a path ending in .pkl
    ("bots/team_a.pkl" -> Player("team_a", "bots/team_a.pkl")) or a dummy
    name ("call" -> Player("call", "dummy:call")).
    """
    if spec.endswith(".pkl"):
        stem = os.path.splitext(os.path.basename(spec))[0]
        return Player(stem, spec)
    # Read at call time, so tests can substitute their own dummies.
    if spec in dummies.DUMMIES:
        return Player(spec, DUMMY + spec)
    raise ValueError(
        f"{spec!r} is neither a .pkl file nor a dummy "
        f"({', '.join(dummies.DUMMIES)})"
    )


def build(p: Player, seed: int, timeout: float, launcher=(),
          memory_mb: int = 2048):
    """A fresh model for p: a RemoteModel for a pickle, or the dummy bot
    built with seed."""
    if p.source.endswith(".pkl"):
        return RemoteModel(p.source, timeout, launcher, memory_mb)
    if p.source.startswith(DUMMY):
        name = p.source[len(DUMMY):]
        if name in dummies.DUMMIES:
            return dummies.DUMMIES[name](seed=seed)
    raise ValueError(f"{p.name}: unknown source {p.source!r}")

"""
Checks that your model (model.Model) is a valid submission: it pickles,
and played from that pickle in its own worker process, as in the arena, it
plays every dummy bot without faults and within the time limit.

    .venv/bin/python -m pytest tests/test_model.py -q

A failure names the fault (an exception in seed, run or end_game, an
illegal move, or a timeout). This checks validity, not strength: use
python -m evals for that.
"""

import pickle

import pytest

from arena.dummies import DUMMIES
from arena.match import MatchConfig, play_match
from arena.players import player
from evals.score import score
from model import Model


@pytest.fixture(scope="module")
def you(tmp_path_factory):
    """Your model pickled once, like a submission."""
    path = tmp_path_factory.mktemp("you") / "you.pkl"
    path.write_bytes(pickle.dumps(Model()))
    return player(str(path))


def _check(players, **config):
    result = score([play_match(players, MatchConfig(**config))])
    assert result.faults[0] == 0, result.first_faults[0]


def test_model_pickles():
    model = pickle.loads(pickle.dumps(Model()))
    assert hasattr(model, "seed") and hasattr(model, "run")


@pytest.mark.parametrize("opponent", list(DUMMIES))
def test_plays_legally_heads_up(you, opponent):
    _check([you, player(opponent)], deals=10)


def test_plays_legally_at_a_full_table(you):
    # EquityBot is left out: it is slow with many players.
    others = [player(name) for name in DUMMIES if name != "equity"]
    _check([you] + others, deals=4)


def test_plays_legally_with_short_stacks(you):
    # Short stacks reach all-ins, side pots and "cannot raise" cheaply.
    others = [player(name) for name in ("shove", "random", "call")]
    _check([you] + others, deals=10, stack=60)

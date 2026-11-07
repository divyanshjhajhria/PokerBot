"""Adversarial engine tests: models that forge or tamper with actions."""

import copy
import threading

import pytest

from engine import play_hand
from game_state import GameState, InvalidAction
from player_action import DEALER, PlayerAction as A
from poker_test_helpers import (
    Scripted, check_result, deck_from, std_deck,
)

KINDS = {"fold", "call", "raise", "reveal_card", "win"}
TIMEOUT = 10


def bounded(fn, *args, **kwargs):
    """Runs fn in a daemon thread; fails if it is still running after
    TIMEOUT seconds, re-raises anything it raised."""
    box = {}

    def target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(TIMEOUT)
    if t.is_alive():
        pytest.fail(f"play_hand still running after {TIMEOUT}s (hang)")
    if "error" in box:
        raise box["error"]
    return box["value"]


def forge(action, **fields):
    for name, value in fields.items():
        object.__setattr__(action, name, value)
    return action


def assert_clean(result, deck, stacks, **kw):
    for a in result.actions:
        assert type(a) is A
        assert a.kind in KINDS
        if a.kind == "reveal_card":
            assert a.card is not None and a.player_id >= DEALER
        elif a.kind == "win":
            assert a.player_id >= 0 and a.card is None and a.amount >= 1
        else:
            assert a.player_id >= 0 and a.card is None
    check_result(result, deck, stacks, **kw)


def hu(bad, owing):
    """Heads-up: seat 0 calls (nothing owed by seat 1) or raises to 60
    (seat 1 owes); seat 1 returns `bad` on its first turn, then calls."""
    deck = std_deck()
    first = ("raise", 60) if owing else "call"
    models = [Scripted([first]), Scripted([("return", bad)])]
    result = bounded(play_hand, models, deck, [500, 500])
    replacement = A.fold(1) if owing else A.call(1)
    assert result.actions[1] == replacement
    assert type(result.actions[1]) is A
    assert set(result.faults) == {1}
    assert isinstance(result.faults[1], str) and result.faults[1]
    assert_clean(result, deck, [500, 500])
    return result


# -- forged PlayerActions ----------------------------------------------------

def _forged():
    return {
        "kind bogus": lambda: forge(A.call(1), kind="bogus"),
        "kind bogus with raise amount": lambda: forge(A.raise_to(1, 300),
                                                      kind="bogus"),
        "negative raise amount": lambda: forge(A.raise_to(1, 100),
                                               amount=-5),
        "zero raise amount": lambda: forge(A.raise_to(1, 100), amount=0),
        "sub-minimum amount, kind bogus": lambda: forge(A.raise_to(1, 25),
                                                        kind="bogus"),
        "call with an amount": lambda: forge(A.call(1), amount=50),
        "non-dealer reveal_card": lambda: forge(A.call(1),
                                                kind="reveal_card"),
        "reveal_card as dealer": lambda: forge(A.call(1), player_id=DEALER,
                                               kind="reveal_card",
                                               card=std_deck()[51]),
        "player_id not an int": lambda: forge(A.call(1), player_id="1"),
        "amount not an int": lambda: forge(A.raise_to(1, 100),
                                           amount=100.0),
    }


@pytest.mark.parametrize("owing", [False, True], ids=["check", "fold"])
@pytest.mark.parametrize("name", list(_forged()))
def test_A01_forged_action_replaced(name, owing):
    """A-01: a PlayerAction mutated after construction (bad kind, amount, card or id) is a fault, replaced by check/fold; only valid PlayerActions are recorded and the hand ends."""
    hu(_forged()[name](), owing)


def test_A02_forged_kind_cannot_crash_or_hang_multiway():
    """A-02: forged actions from every seat on every turn never crash or hang play_hand; invariants hold."""
    deck = std_deck()

    class Forger(Scripted):
        def run(self, actions, gs):
            super().run(actions, gs)
            return forge(A.raise_to(self.player_id, 30), kind="bogus")

    models = [Forger() for _ in range(4)]
    result = bounded(play_hand, models, deck, [500] * 4)
    assert set(result.faults) == {3, 0, 1}  # BB (seat 2) wins unasked
    assert_clean(result, deck, [500] * 4)


def test_A03_mutating_a_returned_action_later_changes_nothing():
    """A-03: mutating an action after returning it does not change HandResult.actions or what other seats received."""
    deck = std_deck()

    class Mutator(Scripted):
        returned = []

        def run(self, actions, gs):
            for old in self.returned:
                forge(old, kind="bogus", amount=-1)
            act = super().run(actions, gs)
            self.returned.append(act)
            return act

    ref_models = [Scripted(), Scripted()]
    ref = bounded(play_hand, ref_models, deck, [500, 500])
    models = [Scripted(), Mutator()]
    Mutator.returned = []
    result = bounded(play_hand, models, deck, [500, 500])
    assert result.faults == {}
    assert result.actions == ref.actions
    assert [s["actions"] for s in models[0].runs] == [
        s["actions"] for s in ref_models[0].runs]
    assert_clean(result, deck, [500, 500])


def test_A10_only_rebuilt_copy_is_applied():
    """A-10: the engine applies a fresh PlayerAction rebuilt from player_id, kind and amount, so a stray card set on a legal call is dropped, not recorded."""
    deck = std_deck()
    returned = forge(A.call(1), card=std_deck()[51])
    models = [Scripted(), Scripted([("return", returned)])]
    result = bounded(play_hand, models, deck, [500, 500])
    assert result.actions[1] == A.call(1)
    assert type(result.actions[1]) is A
    assert result.actions[1] is not returned
    assert all(a.card is None for a in result.actions
               if a.kind != "reveal_card")
    assert_clean(result, deck, [500, 500])


# -- subclasses and impostors ------------------------------------------------

class HonestSub(A):
    pass


class Shifty(A):
    """Reads of player_id and kind change every time."""

    def __getattribute__(self, name):
        if name in ("player_id", "kind", "amount"):
            n = object.__getattribute__(self, "__dict__").setdefault(
                "_reads", 0)
            object.__getattribute__(self, "__dict__")["_reads"] = n + 1
            if name == "player_id":
                return (1, 0, DEALER, 1)[n % 4]
            if name == "kind":
                return ("call", "raise", "bogus", "fold")[n % 4]
            return (0, 10_000, -3, 0)[n % 4]
        if name == "__class__":
            return A
        return object.__getattribute__(self, name)


class Impostor:
    """Not a PlayerAction at all, but claims to be one."""

    player_id = 1
    kind = "call"
    amount = 0
    card = None

    @property
    def __class__(self):
        return A


def test_A04_honest_subclass_is_a_fault():
    """A-04: returning an instance of a PlayerAction subclass, even a legal one, is a fault replaced by check/fold."""
    hu(HonestSub.call(1), owing=False)
    hu(HonestSub.raise_to(1, 200), owing=True)


@pytest.mark.parametrize("owing", [False, True], ids=["check", "fold"])
def test_A05_shifting_subclass_is_a_fault(owing):
    """A-05: a subclass whose player_id/kind/amount change between reads (and lies about __class__) causes no crash, a fault and check/fold."""
    hu(Shifty(1, "call"), owing)


@pytest.mark.parametrize("owing", [False, True], ids=["check", "fold"])
def test_A06_impostor_is_a_fault(owing):
    """A-06: a non-PlayerAction object that claims __class__ is PlayerAction is a fault replaced by check/fold."""
    hu(Impostor(), owing)


# -- exceptions that cannot be described -------------------------------------

class Nasty(Exception):
    def __str__(self):
        raise RuntimeError("str fails")

    def __repr__(self):
        raise RuntimeError("repr fails")


class NastyStrType(Exception):
    def __str__(self):
        return 42  # str() raises TypeError


@pytest.mark.parametrize("exc", [Nasty, NastyStrType, RuntimeError],
                         ids=["str raises", "str not a string", "empty"])
def test_A07_undescribable_run_exception(exc):
    """A-07: run raising an exception whose str() raises (or is empty) still completes the hand with a non-empty fault string."""
    deck = std_deck()
    models = [Scripted(), Scripted([("exc", exc())])]
    result = bounded(play_hand, models, deck, [500, 500])
    assert result.actions[1] == A.call(1)
    assert isinstance(result.faults[1], str) and result.faults[1]
    assert_clean(result, deck, [500, 500])


@pytest.mark.parametrize("exc", [Nasty, NastyStrType, RuntimeError],
                         ids=["str raises", "str not a string", "empty"])
def test_A08_undescribable_seed_exception(exc):
    """A-08: seed raising an exception whose str() raises (or is empty) still completes the hand with a non-empty fault string."""

    class BadSeed(Scripted):
        def seed(self, hand, player_id):
            raise exc()

    deck = deck_from("7C 2D", "AS AH", board="3S 8D 9H JC 4S")
    models = [Scripted(), BadSeed()]
    result = bounded(play_hand, models, deck, [500, 500])
    assert models[1].runs == []
    assert isinstance(result.faults[1], str) and result.faults[1]
    assert result.net == [-20, 20]
    assert_clean(result, deck, [500, 500])


# -- contract: GameState rejects unknown kinds -------------------------------

def _states():
    fresh = GameState([500, 500, 500])
    raised = GameState([500, 500, 500])
    raised.process_action(A.raise_to(0, 60))
    flop = GameState([500, 500])
    flop.process_action(A.call(0))
    flop.process_action(A.call(1))
    for c in std_deck()[40:43]:
        flop.process_action(A.reveal_card(c))
    return {"preflop": fresh, "after raise": raised, "flop": flop}


@pytest.mark.parametrize("kind", ["bogus", "reveal_card", "", "FOLD"])
@pytest.mark.parametrize("which", ["preflop", "after raise", "flop"])
def test_A09_gamestate_rejects_unknown_kind(which, kind):
    """A-09: validate_action and process_action raise InvalidAction for a non-dealer action whose kind is not fold/call/raise, leaving the state unchanged."""
    state = _states()[which]
    me = state.to_act
    for base in (A.call(me), A.raise_to(me, state.min_raise_to)):
        forged = forge(copy.copy(base), kind=kind)
        before = copy.deepcopy(vars(state))
        with pytest.raises(InvalidAction):
            state.validate_action(forged)
        with pytest.raises(InvalidAction):
            state.process_action(forged)
        assert vars(state) == before

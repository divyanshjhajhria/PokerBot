"""Tests for end-of-hand events (win, player reveals, end_game)."""

import copy

import pytest
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

from card_utils import Card
from engine import payouts, play_hand
from game_interface import GameInterface
from game_state import GameState, GameStateView, InvalidAction
from player_action import DEALER, PlayerAction as A
from poker_test_helpers import (
    RandomBot, Scripted, deck_from, std_deck,
)

NEUTRAL = "2C 7D 9H JC 4S"
C = Card.parse


class Boom(BaseException):
    pass


class Nasty(Exception):
    def __str__(self):
        raise RuntimeError("str fails")

    def __repr__(self):
        raise RuntimeError("repr fails")


class EndRecorder(Scripted):
    """Scripted, plus end_game recording; optional shared log."""

    def __init__(self, *args, end_exc=None, end_mutate=False, **kw):
        super().__init__(*args, **kw)
        self.ends = []
        self.end_raw = []
        self.end_exc = end_exc
        self.end_mutate = end_mutate

    def run(self, actions, gs):
        if self.log is not None:
            self.log.append(("run", self.player_id))
        return super().run(actions, gs)

    def end_game(self, actions, gs):
        self.ends.append({"actions": list(actions),
                          "over": gs.is_hand_over,
                          "stacks": tuple(gs.stacks), "pot": gs.pot,
                          "view": type(gs)})
        self.end_raw.append(actions)
        if self.log is not None:
            self.log.append(("end", self.player_id))
        if self.end_mutate:
            actions.clear()
            actions.append("junk")
        if self.end_exc is not None:
            raise self.end_exc


class SeedRaiserWithEnd(EndRecorder):
    def seed(self, hand, player_id):
        super().seed(hand, player_id)
        raise RuntimeError("seed fails")


def R(text, seat):
    return A.reveal_card(C(text), player_id=seat)


def ends(action):
    return action.kind == "win" or (action.kind == "reveal_card"
                                    and action.player_id >= 0)


def tail_of(result):
    """The end-of-hand actions (player reveals and wins)."""
    i = len(result.actions)
    while i > 0 and ends(result.actions[i - 1]):
        i -= 1
    return result.actions[i:]


def snapshot(state):
    return copy.deepcopy(vars(state))


# -- PlayerAction --------------------------------------------------------------

def test_PA01_win_constructor():
    """PA-01: PlayerAction.win(seat, amount) is kind "win" with that player_id and amount and no card."""
    a = A.win(2, 35)
    assert (a.player_id, a.kind, a.amount, a.card) == (2, "win", 35, None)
    assert type(a) is A
    assert A.win(0, 1).amount == 1


@pytest.mark.parametrize("make", [
    lambda: A.win(0, 0), lambda: A.win(0, -5), lambda: A.win(DEALER, 10),
    lambda: A.win(0, True), lambda: A.win(0, "5"), lambda: A.win(0, 5.0),
    lambda: A.win("0", 5),
    lambda: A(0, "win", 10, C("AS")),
], ids=["zero", "negative", "dealer", "bool", "str", "float", "str id",
        "card"])
def test_PA02_win_invalid(make):
    """PA-02: win needs a non-dealer winner, a strict int amount >= 1 and no card; otherwise ValidationError."""
    with pytest.raises(ValidationError):
        make()


def test_PA03_reveal_card_backward_compatible():
    """PA-03: reveal_card(card) is still the dealer's; reveal_card(card, player_id=DEALER) and reveal_card(card, DEALER) are equal to it."""
    a = A.reveal_card(C("AS"))
    assert (a.player_id, a.kind, a.amount, a.card) == (
        DEALER, "reveal_card", 0, C("AS"))
    assert A.reveal_card(C("AS"), player_id=DEALER) == a
    assert A.reveal_card(C("AS"), DEALER) == a


def test_PA04_player_reveal_card():
    """PA-04: reveal_card(card, player_id=seat) (keyword or positional) is a reveal from that seat."""
    a = A.reveal_card(C("KD"), player_id=3)
    assert (a.player_id, a.kind, a.amount, a.card) == (
        3, "reveal_card", 0, C("KD"))
    assert A.reveal_card(C("KD"), 3) == a


@pytest.mark.parametrize("make", [
    lambda: A(3, "reveal_card"), lambda: A(3, "reveal_card", 5, C("AS")),
    lambda: A(DEALER, "reveal_card", 5, C("AS")),
    lambda: A.reveal_card(None, player_id=3), lambda: A.reveal_card(None),
    lambda: A.reveal_card("AS", player_id=3),
], ids=["player no card", "player amount", "dealer amount",
        "player None", "dealer None", "str card"])
def test_PA05_reveal_card_invalid(make):
    """PA-05: reveal_card always needs a Card and no amount, from the dealer or a player; otherwise ValidationError."""
    with pytest.raises(ValidationError):
        make()


@pytest.mark.parametrize("make", [
    lambda: A.fold(DEALER), lambda: A.call(DEALER),
    lambda: A.raise_to(DEALER, 40), lambda: A(0, "fold", 0, C("AS")),
    lambda: A(0, "call", 5), lambda: A.raise_to(0, 0),
    lambda: A(DEALER, "win", 5),
], ids=["dealer fold", "dealer call", "dealer raise", "fold card",
        "call amount", "raise 0", "dealer win"])
def test_PA06_existing_rules_unchanged(make):
    """PA-06: the existing rules still hold: no betting from the dealer, no card on fold/call/raise, no amount on fold/call, raise > 0, and the dealer cannot win."""
    with pytest.raises(ValidationError):
        make()


# -- GameState -----------------------------------------------------------------

def _not_over_states():
    pre = GameState([500, 500, 500])
    closed = GameState([500, 500])
    closed.process_action(A.call(0))
    closed.process_action(A.call(1))  # to_act None, no board yet
    flop = GameState([500, 500])
    for a in (A.call(0), A.call(1)):
        flop.process_action(a)
    for c in std_deck()[40:43]:
        flop.process_action(A.reveal_card(c))
    turn_closed = GameState([500, 500])
    for a in (A.call(0), A.call(1)):
        turn_closed.process_action(a)
    for c in std_deck()[40:43]:
        turn_closed.process_action(A.reveal_card(c))
    turn_closed.process_action(A.call(1))
    turn_closed.process_action(A.call(0))
    turn_closed.process_action(A.reveal_card(std_deck()[43]))
    turn_closed.process_action(A.call(1))
    turn_closed.process_action(A.call(0))
    assert not turn_closed.is_hand_over and turn_closed.to_act is None
    return {"preflop": pre, "street closed": closed, "flop": flop,
            "turn closed": turn_closed}


def showdown_state(stacks=(500, 500)):
    """Everyone checks down to a showdown."""
    state = GameState(list(stacks))
    cards = iter(std_deck()[40:45])
    while not state.is_hand_over:
        if state.is_street_closed:
            state.process_action(A.reveal_card(next(cards)))
        else:
            state.process_action(A.call(state.to_act))
    return state


def foldout_state():
    state = GameState([500, 500, 500])  # button 0: UTG 0, SB 1, BB 2
    state.process_action(A.fold(0))
    state.process_action(A.fold(1))
    assert state.is_hand_over
    return state


@pytest.mark.parametrize("which", list(_not_over_states()))
def test_GS01_end_actions_illegal_before_hand_over(which):
    """GS-01: before the hand is over, a player reveal_card or a win raises InvalidAction and changes nothing."""
    state = _not_over_states()[which]
    for seat in range(state.num_players):
        for action in (A.win(seat, 10), A.reveal_card(C("AS"), player_id=seat)):
            before = snapshot(state)
            with pytest.raises(InvalidAction):
                state.process_action(action)
            assert snapshot(state) == before


def test_GS02_validate_action_rejects_end_kinds():
    """GS-02: validate_action rejects win and player reveal_card from the player to act."""
    state = GameState([500, 500, 500])
    me = state.to_act
    for action in (A.win(me, 10), A.reveal_card(C("AS"), player_id=me)):
        with pytest.raises(InvalidAction):
            state.validate_action(action)


def test_GS03_win_credits_stack_only():
    """GS-03: after a showdown, win(seat, x) adds x to stacks[seat] and changes nothing else; wins add up."""
    state = showdown_state()
    before = snapshot(state)
    state.process_action(A.win(1, 37))
    state.process_action(A.win(1, 3))
    state.process_action(A.win(0, 5))
    after = snapshot(state)
    expected = copy.deepcopy(before)
    expected["stacks"] = [before["stacks"][0] + 5, before["stacks"][1] + 40]
    assert after == expected
    assert state.is_hand_over


def test_GS04_win_to_folded_or_unknown_seat():
    """GS-04: a win to a folded seat or a seat outside 0..n-1 raises InvalidAction and changes nothing."""
    state = foldout_state()
    for action in (A.win(0, 10), A.win(1, 10), A.win(3, 10),
                   A.win(99, 10)):
        before = snapshot(state)
        with pytest.raises(InvalidAction):
            state.process_action(action)
        assert snapshot(state) == before
    forged = A.win(2, 10)
    object.__setattr__(forged, "player_id", -2)
    before = snapshot(state)
    with pytest.raises(InvalidAction):
        state.process_action(forged)
    assert snapshot(state) == before


def test_GS05_fold_out_survivor_credited():
    """GS-05: after a fold-out the survivor can be paid; is_hand_over stays True."""
    state = foldout_state()
    pot = state.pot
    state.process_action(A.win(2, pot))
    assert state.stacks == [500, 490, 480 + pot]
    assert state.pot == pot and state.is_hand_over


def test_GS06_player_reveal_after_hand_over():
    """GS-06: after the hand, a reveal_card from a seat still in (zero-stack included) changes nothing; from a folded or unknown seat it raises InvalidAction."""
    state = showdown_state((0, 500, 500))
    for seat in (0, 1, 2):
        before = snapshot(state)
        state.process_action(R("AS", seat))
        state.process_action(R("AH", seat))
        assert snapshot(state) == before
    folded = foldout_state()
    for seat in (0, 1, 3):
        before = snapshot(folded)
        with pytest.raises(InvalidAction):
            folded.process_action(R("AS", seat))
        assert snapshot(folded) == before
    before = snapshot(folded)
    folded.process_action(R("AS", 2))
    assert snapshot(folded) == before


def test_GS07_betting_and_dealer_still_illegal_after_hand():
    """GS-07: after the hand is over (also after wins), fold/call/raise and dealer reveal_card still raise InvalidAction."""
    state = showdown_state()
    state.process_action(A.win(1, 40))
    for action in (A.fold(0), A.call(1), A.raise_to(0, 100),
                   A.reveal_card(C("2C"))):
        before = snapshot(state)
        with pytest.raises(InvalidAction):
            state.process_action(action)
        assert snapshot(state) == before


# -- GameInterface -------------------------------------------------------------

class GIModel:
    def __init__(self, end=True, returns=None, exc=None):
        self.calls = []
        self.returns = returns
        self.exc = exc
        if end:
            self.end_game = self._end

    def seed(self, hand, player_id):
        self.player_id = player_id

    def run(self, actions, gs):
        return A.call(self.player_id)

    def _end(self, actions, gs):
        self.calls.append((actions, gs))
        if self.exc is not None:
            raise self.exc
        return self.returns


def _gi(model):
    state = GameState([500, 500])
    return GameInterface(model, GameStateView(state), 0,
                         [C("AS"), C("AH")]), state


def test_GI01_end_game_forwards():
    """GI-01: end_game(actions) calls model.end_game(actions, game_state) once with the same list and the interface's view, and returns None."""
    model = GIModel(returns=42)
    gi, _ = _gi(model)
    acts = [A.call(0)]
    assert gi.end_game(acts) is None
    assert len(model.calls) == 1
    assert model.calls[0][0] is acts
    assert model.calls[0][1] is gi.game_state


@pytest.mark.parametrize("value", ["missing", None, 5])
def test_GI02_no_callable_end_game(value):
    """GI-02: a model without a callable end_game is left alone: end_game returns None without error."""
    model = GIModel(end=False)
    if value != "missing":
        model.end_game = value
    gi, _ = _gi(model)
    assert gi.end_game([A.call(0)]) is None


@pytest.mark.parametrize("exc", [ValueError("x"), Boom()])
def test_GI03_end_game_exceptions_propagate(exc):
    """GI-03: exceptions (including BaseExceptions) from model.end_game propagate unchanged."""
    gi, _ = _gi(GIModel(exc=exc))
    with pytest.raises(type(exc)):
        gi.end_game([])


def test_GI04_end_game_not_called_by_other_methods():
    """GI-04: __init__ and process_action never call model.end_game."""
    model = GIModel()
    gi, state = _gi(model)
    gi.process_action([])
    assert model.calls == []


def test_GI05_template_model_end_game():
    """GI-05: the model.py template plays through GameInterface and its end_game runs without error; seed() sets the per-hand fields again next hand."""
    from model import Model

    model = Model()
    gi, _ = _gi(model)
    assert gi.end_game([]) is None
    hand = model.hand
    gi, _ = _gi(model)  # the next hand seeds the same model again
    assert model.hand is not hand and model.player_id is not None


# -- engine --------------------------------------------------------------------

def hu_deck():
    return deck_from("7C 2D", "AS AH", board="3S 8D 9H JC 4S")


def check_awards(result, stacks, button=0):
    """Replays; checks phases, final stacks, and win totals vs payouts."""
    n = len(stacks)
    state = GameState(list(stacks))
    state.setup_hand(list(stacks), button, 10, 20)
    contributed = None
    for a in result.actions:
        if state.is_hand_over:
            if contributed is None:
                contributed = [stacks[i] - state.stacks[i] for i in range(n)]
            assert ends(a)
        else:
            assert not ends(a)
        state.process_action(a)
    assert state.is_hand_over
    if contributed is None:
        contributed = [stacks[i] - state.stacks[i] for i in range(n)]
    assert state.stacks == [s + d for s, d in zip(stacks, result.net)]
    pay = payouts(contributed, list(state.folded), result.hands,
                  result.board, button)
    wins = [a for a in result.actions if a.kind == "win"]
    assert len({w.player_id for w in wins}) == len(wins)
    assert {w.player_id: w.amount for w in wins} == {
        s: p for s, p in enumerate(pay) if p > 0}
    assert sum(w.amount for w in wins) == state.pot
    return state


def test_EG01_showdown_reveals_heads_up():
    """EG-01: at a heads-up showdown, seats show both hole cards in dealt order from the left of the button, then the single win."""
    deck = hu_deck()
    result = play_hand([Scripted(), Scripted()], deck, [500, 500])
    assert tail_of(result) == [R("AS", 1), R("AH", 1), R("7C", 0),
                               R("2D", 0), A.win(1, 40)]
    check_awards(result, [500, 500])


def test_EG02_folded_seats_do_not_show():
    """EG-02: only unfolded seats show, in seat order from the left of the button (button 2: seats 0, 1, 2)."""
    deck = deck_from("KS KH", "AS AH", "8C 3D", board=NEUTRAL)
    # button 2: SB 0, BB 1, UTG 2. UTG calls, SB folds, BB checks.
    models = [Scripted(["fold"]), Scripted(), Scripted()]
    result = play_hand(models, deck, [500, 500, 500], button=2)
    assert tail_of(result) == [R("AS", 1), R("AH", 1), R("8C", 2),
                               R("3D", 2), A.win(1, 50)]
    check_awards(result, [500, 500, 500], button=2)


def test_EG03_reveals_after_run_out():
    """EG-03: after an all-in run-out, player reveals come after all 5 board cards."""
    deck = hu_deck()
    result = play_hand([Scripted(["allin"]), Scripted(["call"])], deck,
                       [500, 500])
    dealer = [i for i, a in enumerate(result.actions)
              if a.kind == "reveal_card" and a.player_id == DEALER]
    player = [i for i, a in enumerate(result.actions)
              if a.kind == "reveal_card" and a.player_id >= 0]
    assert len(dealer) == 5 and len(player) == 4
    assert max(dealer) < min(player)
    assert result.actions[-1] == A.win(1, 1000)


def test_EG04_fold_out_single_win_no_reveals():
    """EG-04: when everyone else folds, nobody shows and the survivor gets one win of the whole pot."""
    r = play_hand([Scripted([("raise", 60)]), Scripted(["fold"])],
                  std_deck(), [500, 500])
    assert r.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    r = play_hand([Scripted(["fold"]) for _ in range(3)], std_deck(),
                  [500, 500, 500])
    assert r.actions == [A.fold(0), A.fold(1), A.win(2, 30)]
    r = play_hand([Scripted(["fold"]), Scripted()], std_deck(), [500, 500])
    assert r.actions == [A.fold(0), A.win(1, 30)]


def test_EG05_side_pots_one_win_per_seat():
    """EG-05: side pots give one win per paid seat with its total, in seat order from the left of the button: [win(1,200), win(2,300), win(0,100)]."""
    deck = deck_from("QS QH", "KS KH", "AS AH", board=NEUTRAL)
    models = [Scripted(["allin"]), Scripted(["call"]), Scripted(["call"])]
    result = play_hand(models, deck, [300, 200, 100])
    assert tail_of(result) == [
        R("KS", 1), R("KH", 1), R("AS", 2), R("AH", 2), R("QS", 0),
        R("QH", 0), A.win(1, 200), A.win(2, 300), A.win(0, 100)]
    check_awards(result, [300, 200, 100])


def test_EG06_split_pot_odd_chip():
    """EG-06: a split pot with an odd chip gives [win(2,13), win(0,12)] after the reveals (blinds 5/10, SB folded)."""
    deck = deck_from("2C 3D", "4C 5D", "6C 7D", board="AS KS QS JS TS")
    models = [Scripted(), Scripted(["fold"]), Scripted()]
    result = play_hand(models, deck, [500, 500, 500], small_blind=5,
                       big_blind=10)
    assert tail_of(result) == [R("6C", 2), R("7D", 2), R("2C", 0),
                               R("3D", 0), A.win(2, 13), A.win(0, 12)]
    assert result.net == [2, -5, 3]


def test_EG07_zero_stack_seat_shows_and_gets_no_win():
    """EG-07: a zero-stack seat shows its cards at showdown but gets no win (no zero-chip wins)."""
    deck = deck_from("AS AH", "7C 2D", "8S 3H", board=NEUTRAL)
    result = play_hand([Scripted() for _ in range(3)], deck, [0, 500, 500])
    assert tail_of(result) == [R("7C", 1), R("2D", 1), R("8S", 2),
                               R("3H", 2), R("AS", 0), R("AH", 0),
                               A.win(1, 40)]
    assert all(a.amount >= 1 for a in result.actions if a.kind == "win")


def test_EG08_EG09_end_game_sees_final_state():
    """EG-08/EG-09: win totals equal payouts and the pot; in end_game every model sees is_hand_over, the final pot and stacks == start + net."""
    deck = deck_from("QS QH", "KS KH", "AS AH", board=NEUTRAL)
    models = [EndRecorder(["allin"]), EndRecorder(["call"]),
              EndRecorder(["call"])]
    result = play_hand(models, deck, [300, 200, 100])
    check_awards(result, [300, 200, 100])
    for m in models:
        assert len(m.ends) == 1
        end = m.ends[0]
        assert end["over"] and end["pot"] == 600
        assert end["stacks"] == (100, 200, 300)
        assert issubclass(end["view"], GameStateView)


def test_EG10_end_game_pending_lists():
    """EG-10: each seat's end_game list is everything since it was last asked, final moves, reveals and wins included; a never-asked seat gets the whole hand."""
    deck = hu_deck()
    models = [EndRecorder(), EndRecorder()]
    result = play_hand(models, deck, [500, 500])
    tail = [R("AS", 1), R("AH", 1), R("7C", 0), R("2D", 0), A.win(1, 40)]
    assert models[0].ends[0]["actions"] == [A.call(0)] + tail
    assert models[1].ends[0]["actions"] == [A.call(1), A.call(0)] + tail

    three = [EndRecorder(["fold"]), EndRecorder(["fold"]), EndRecorder()]
    play_hand(three, std_deck(), [500, 500, 500])
    everything = [A.fold(0), A.fold(1), A.win(2, 30)]
    assert three[0].ends[0]["actions"] == everything
    assert three[1].ends[0]["actions"] == everything[1:]
    assert three[2].ends[0]["actions"] == everything
    assert three[2].runs == []

    zero = [EndRecorder() for _ in range(3)]
    r = play_hand(zero, deck_from("AS AH", "7C 2D", "8S 3H", board=NEUTRAL),
                  [0, 500, 500])
    assert zero[0].runs == []
    assert zero[0].ends[0]["actions"] == r.actions


def test_EG11_end_game_list_is_fresh():
    """EG-11: each end_game list is a new object, not an earlier list or HandResult.actions; mutating it affects nothing else."""
    deck = hu_deck()
    plain = [EndRecorder(), EndRecorder()]
    ref = play_hand(plain, deck, [500, 500])
    models = [EndRecorder(end_mutate=True), EndRecorder()]
    result = play_hand(models, deck, [500, 500])
    end0 = models[0].end_raw[0]
    assert end0 is not result.actions
    assert all(end0 is not k for k in models[0].kept)
    assert models[1].end_raw[0] is not end0
    assert result.actions == ref.actions and result.faults == {}
    assert models[1].ends[0]["actions"] == plain[1].ends[0]["actions"]


def test_EG12_end_game_order():
    """EG-12: end_game is called once per seat, in seat order 0..n-1, after the hand's last run."""
    log = []
    models = [EndRecorder(log=log) for _ in range(3)]
    play_hand(models, std_deck(), [500, 500, 500])
    end_events = [e for e in log if e[0] == "end"]
    assert end_events == [("end", 0), ("end", 1), ("end", 2)]
    first_end = log.index(("end", 0))
    assert all(e[0] == "end" for e in log[first_end:])


def test_EG13_seed_faulted_not_told_run_faulted_told():
    """EG-13: a seat whose seed raised never gets end_game; a seat whose run faulted still does."""
    bad_seed = SeedRaiserWithEnd()
    bad_run = EndRecorder([("exc", RuntimeError("x"))])
    good = EndRecorder()
    result = play_hand([good, bad_seed, bad_run], std_deck(),
                       [500, 500, 500])
    assert bad_seed.ends == []
    assert len(bad_run.ends) == 1 and len(good.ends) == 1
    assert set(result.faults) == {1, 2}


def test_EG14_end_game_exception_is_a_fault():
    """EG-14: an Exception from end_game is that seat's fault (text starts with "end_game"); later seats are still told and net/actions are unchanged; an earlier fault is kept."""
    deck = hu_deck()
    ref = play_hand([EndRecorder() for _ in range(3)], deck,
                    [500, 500, 500])
    models = [EndRecorder(end_exc=ValueError("e0")), EndRecorder(),
              EndRecorder()]
    result = play_hand(models, deck, [500, 500, 500])
    assert list(result.faults) == [0]
    assert result.faults[0].startswith("end_game")
    assert len(models[1].ends) == 1 and len(models[2].ends) == 1
    assert result.actions == ref.actions and result.net == ref.net

    models = [EndRecorder([("return", None)], end_exc=ValueError("late")),
              EndRecorder(), EndRecorder()]
    result = play_hand(models, deck, [500, 500, 500])
    assert not result.faults[0].startswith("end_game")


def test_EG15_undescribable_end_game_exception():
    """EG-15: an end_game exception whose str() raises still gives a non-empty fault text starting with "end_game"."""
    models = [EndRecorder(), EndRecorder(end_exc=Nasty())]
    result = play_hand(models, hu_deck(), [500, 500])
    assert result.faults[1].startswith("end_game")


def test_EG16_base_exception_from_end_game_propagates():
    """EG-16: a BaseException from end_game propagates at once; later seats are not told."""
    models = [EndRecorder(end_exc=Boom()), EndRecorder(), EndRecorder()]
    with pytest.raises(Boom):
        play_hand(models, std_deck(), [500, 500, 500])
    assert models[1].ends == [] and models[2].ends == []


@pytest.mark.parametrize("bad", [
    lambda: A.win(1, 10**6), lambda: A.reveal_card(C("AS"), player_id=1),
], ids=["win", "reveal"])
def test_EG18_models_cannot_forge_end_actions(bad):
    """EG-18: a model returning a win or a player reveal_card on its turn is faulted and replaced by check/fold; it is never applied."""
    models = [Scripted([("raise", 60)]), Scripted([("return", bad())])]
    result = play_hand(models, std_deck(), [500, 500])
    assert result.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    assert set(result.faults) == {1}


# -- properties ----------------------------------------------------------------

class EndRandomBot(RandomBot):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.ends = []

    def end_game(self, actions, gs):
        self.ends.append(list(actions))


@st.composite
def hand_args(draw):
    n = draw(st.integers(2, 5))
    deck = list(draw(st.permutations(std_deck())))
    stacks = draw(st.lists(st.integers(0, 600), min_size=n, max_size=n))
    sb = draw(st.integers(1, 30))
    bb = draw(st.integers(sb, 60))
    button = draw(st.integers(0, n - 1))
    weights = [0, 1, 1, 1, 2, 3, 1, 2, 3, 4, 4, 5, 6, 7, 8, 9]
    scripts = [draw(st.lists(st.sampled_from(weights), min_size=1,
                             max_size=10)) for _ in range(n)]
    seed_faults = [draw(st.sampled_from([False] * 4 + [True]))
                   for _ in range(n)]
    return dict(deck=deck, stacks=stacks, sb=sb, bb=bb, button=button,
                scripts=scripts, seed_faults=seed_faults)


def _play(args):
    models = [EndRandomBot(s, f) for s, f in zip(args["scripts"],
                                                  args["seed_faults"])]
    result = play_hand(models, args["deck"], args["stacks"],
                       button=args["button"], small_blind=args["sb"],
                       big_blind=args["bb"])
    return models, result


@settings(max_examples=50, deadline=None)
@given(hand_args())
def test_EP1_EP2_EP3_replay_awards_reveals(args):
    """EP-1..EP-3: replay ends with stacks == start + net; betting before hand over, only reveals/wins after; one win >= 1 per paid unfolded seat equal to payouts, summing to the pot; reveals of both hole cards by every unfolded seat from the left of the button only at a showdown."""
    _, result = _play(args)
    stacks, button, n = args["stacks"], args["button"], len(args["stacks"])
    state = GameState(list(stacks))
    state.setup_hand(list(stacks), button, args["sb"], args["bb"])
    contributed = None
    for a in result.actions:
        if state.is_hand_over and contributed is None:
            contributed = [stacks[i] - state.stacks[i] for i in range(n)]
        assert ends(a) == state.is_hand_over
        state.process_action(a)
    assert state.is_hand_over
    if contributed is None:
        contributed = [stacks[i] - state.stacks[i] for i in range(n)]
    assert state.stacks == [s + d for s, d in zip(stacks, result.net)]
    order = [(button + k) % n for k in range(1, n + 1)]
    alive = [s for s in order if not state.folded[s]]
    pay = payouts(contributed, list(state.folded), result.hands,
                  result.board, button)
    reveals = [] if len(alive) == 1 else [
        A.reveal_card(c, player_id=s) for s in alive for c in result.hands[s]]
    wins = [A.win(s, pay[s]) for s in order if pay[s] > 0]
    assert tail_of(result) == reveals + wins
    assert sum(w.amount for w in wins) == state.pot


@settings(max_examples=50, deadline=None)
@given(hand_args())
def test_EP4_runs_plus_end_game_cover_the_hand(args):
    """EP-4: for each seat whose seed did not raise, its run lists plus its end_game list equal HandResult.actions; seed-faulted seats get no end_game."""
    models, result = _play(args)
    for seat, m in enumerate(models):
        if args["seed_faults"][seat]:
            assert m.ends == []
            continue
        assert len(m.ends) == 1
        seen = [a for rec in m.runs for a in rec["actions"]] + m.ends[0]
        assert seen == result.actions

import copy

import pytest

from engine import play_hand
from game_state import GameStateView
from player_action import PlayerAction as A
from poker_test_helpers import (
    Scripted, SeedRaiser, check_result, deck_from, std_deck,
)

NEUTRAL = "2C 7D 9H JC 4S"


def hu_deck():
    # seat 0 weak, seat 1 strong
    return deck_from("7C 2D", "AS AH", board="3S 8D 9H JC 4S")


# -- setup and validation ----------------------------------------------------

def _invalid_cases():
    d = std_deck()
    return {
        "S-01 len mismatch": dict(n_models=3, deck=d, stacks=[500, 500]),
        "S-02 short deck": dict(n_models=2, deck=d[:8], stacks=[500, 500]),
        "S-02 short deck 3 seats": dict(n_models=3, deck=d[:10],
                                        stacks=[500, 500, 500]),
        "S-03 dup in prefix": dict(n_models=2, deck=[d[0]] + d[:51],
                                   stacks=[500, 500]),
        "S-03 dup in tail": dict(n_models=2, deck=d + [d[0]],
                                 stacks=[500, 500]),
        "S-04 button": dict(n_models=2, deck=d, stacks=[500, 500], button=2),
        "S-04 sb > bb": dict(n_models=2, deck=d, stacks=[500, 500],
                             small_blind=30, big_blind=20),
        "S-04 negative stack": dict(n_models=2, deck=d, stacks=[-1, 500]),
        "S-04 one seat": dict(n_models=1, deck=d, stacks=[500]),
        "S-04 teams length": dict(n_models=2, deck=d, stacks=[500, 500],
                                  teams=["a"]),
        "S-04 teams duplicate": dict(n_models=2, deck=d, stacks=[500, 500],
                                     teams=["a", "a"]),
    }


@pytest.mark.parametrize("case", list(_invalid_cases()))
def test_S09_validation_before_any_seed(case):
    """S-09 (S-01..S-04): mismatched lengths, short or duplicate decks and invalid setup values raise ValueError before any model's seed is called."""
    kw = dict(_invalid_cases()[case])
    models = [Scripted() for _ in range(kw.pop("n_models"))]
    with pytest.raises(ValueError):
        play_hand(models, **kw)
    assert all(m.seeds == [] for m in models)


def test_S02_deck_of_exactly_2n_plus_5_is_accepted():
    """S-02: a deck of exactly 2n+5 cards is enough for a full hand."""
    deck = std_deck()[:9]
    result = play_hand([Scripted(), Scripted()], deck, [500, 500])
    assert len(result.board) == 5
    check_result(result, deck, [500, 500])


def test_S05_H01_seeds_and_hands_from_deck():
    """S-05/H-01: seat i is seeded with [deck[2i], deck[2i+1]] and player id i; hands[i] matches."""
    deck = std_deck()
    models = [Scripted() for _ in range(3)]
    result = play_hand(models, deck, [500, 500, 500])
    for i, m in enumerate(models):
        assert m.seeds == [([deck[2 * i], deck[2 * i + 1]], i)]
        assert result.hands[i] == [deck[2 * i], deck[2 * i + 1]]


def test_S06_view_reflects_setup_arguments():
    """S-06: run gets a GameStateView with the given button, blinds and teams, blinds already posted."""
    models = [Scripted() for _ in range(3)]
    play_hand(models, std_deck(), [400, 500, 600], button=1, small_blind=5,
              big_blind=10, teams=["x", "y", "z"])
    first = models[1].runs[0]  # button 1: SB seat 2, BB seat 0, seat 1 first
    assert issubclass(first["view_type"], GameStateView)
    assert first["button"] == 1
    assert (first["small_blind"], first["big_blind"]) == (5, 10)
    assert tuple(first["teams"]) == ("x", "y", "z")
    assert tuple(first["stacks"]) == (390, 500, 595)
    for m in models:
        for snap in m.runs:
            assert issubclass(snap["view_type"], GameStateView)


def test_S07_views_share_one_state():
    """S-07: a seat's view at its turn reflects moves other seats made earlier."""
    models = [Scripted([("raise", 60)]), Scripted(["fold"])]
    play_hand(models, std_deck(), [500, 500])
    snap = models[1].runs[0]
    assert tuple(snap["bets"]) == (60, 20)
    assert snap["call_amount"] == 40


def test_S08_inputs_not_mutated():
    """S-08: the caller's deck, stacks, teams and models lists are unchanged."""
    deck = std_deck()
    stacks = [300, 500, 700]
    teams = ["a", "b", "c"]
    models = [Scripted(["allin"]), Scripted(["call"]), Scripted(["call"])]
    saved = (list(deck), list(stacks), list(teams), list(models))
    play_hand(models, deck, stacks, teams=teams)
    assert deck == saved[0]
    assert stacks == saved[1]
    assert teams == saved[2]
    assert len(models) == 3 and all(a is b for a, b in zip(models, saved[3]))


# -- routing -----------------------------------------------------------------

def test_R01_run_only_on_own_turn():
    """R-01: run is only called when the view's to_act is the model's own seat."""
    models = [Scripted() for _ in range(3)]
    play_hand(models, std_deck(), [500, 500, 500])
    for i, m in enumerate(models):
        assert m.runs
        assert all(s["to_act"] == i for s in m.runs)


def test_R02_blinds_are_not_actions():
    """R-02: the first preflop actor's first actions list is []; later seats get the moves before them."""
    hu = [Scripted(), Scripted()]
    play_hand(hu, std_deck(), [500, 500])
    assert hu[0].runs[0]["actions"] == []

    three = [Scripted() for _ in range(3)]  # button 0: SB 1, BB 2, UTG 0
    play_hand(three, std_deck(), [500, 500, 500])
    assert three[0].runs[0]["actions"] == []
    assert three[1].runs[0]["actions"] == [A.call(0)]
    assert three[2].runs[0]["actions"] == [A.call(0), A.call(1)]


def test_R03_pending_is_everything_since_last_turn():
    """R-03: actions holds everything since the seat's last turn, oldest first, own move and reveals included."""
    deck = std_deck()
    models = [Scripted(), Scripted()]
    play_hand(models, deck, [500, 500])
    flop = [A.reveal_card(c) for c in deck[4:7]]
    assert models[1].runs[0]["actions"] == [A.call(0)]
    assert models[1].runs[1]["actions"] == [A.call(1)] + flop
    assert models[0].runs[1]["actions"] == (
        [A.call(0), A.call(1)] + flop + [A.call(1)])


def test_R04_board_dealt_in_deck_order():
    """R-04: the board comes from deck[2n] onwards, one card per reveal_card, in order."""
    deck = std_deck()
    models = [Scripted() for _ in range(3)]
    result = play_hand(models, deck, [500, 500, 500])
    assert result.board == deck[6:11]
    assert [a.card for a in result.actions
            if a.kind == "reveal_card" and a.player_id == -1] == deck[6:11]


def test_R05_all_in_run_out_asks_nobody():
    """R-05: once both players are all-in, the dealer reveals to the river without asking any model."""
    deck = std_deck()
    models = [Scripted(["allin"]), Scripted(["call"])]
    result = play_hand(models, deck, [500, 500])
    assert len(models[0].runs) == 1 and len(models[1].runs) == 1
    # seat 0 2C 2D, seat 1 2H 2S; board 3C 3D 3H 3S 4C: a split pot
    assert result.actions == [A.raise_to(0, 500), A.call(1)] + [
        A.reveal_card(c) for c in deck[4:9]
    ] + [A.reveal_card(deck[2], player_id=1),
         A.reveal_card(deck[3], player_id=1),
         A.reveal_card(deck[0], player_id=0),
         A.reveal_card(deck[1], player_id=0),
         A.win(1, 500), A.win(0, 500)]


def test_R06_heads_up_order_with_button_1():
    """R-06: heads-up with button 1, seat 1 acts first preflop and seat 0 first on the flop."""
    log = []
    models = [Scripted(log=log), Scripted(log=log)]
    play_hand(models, std_deck(), [500, 500], button=1)
    assert log[0] == (1, "preflop")
    assert [s for s, street in log if street == "flop"][0] == 0


def test_R07_returned_actions_recorded_in_order():
    """R-07: a clean model's returned actions appear unchanged, in order, in HandResult.actions."""
    deck = std_deck()
    scripts = [[("raise", 60), ("raise", 40)], ["fold"], ["call", "call"]]
    models = [Scripted(s) for s in scripts]
    result = play_hand(models, deck, [500, 500, 500])
    r = [A.reveal_card(c) for c in deck[6:11]]
    assert result.actions == (
        [A.raise_to(0, 60), A.fold(1), A.call(2)] + r[:3]
        + [A.call(2), A.raise_to(0, 40), A.call(2)] + r[3:4]
        + [A.call(2), A.call(0)] + r[4:5]
        + [A.call(2), A.call(0)]
        # showdown from the left of the button (seat 1 folded): seat 2's
        # 3C 3D make quads and win the 210 pot
        + [A.reveal_card(deck[4], player_id=2),
           A.reveal_card(deck[5], player_id=2),
           A.reveal_card(deck[0], player_id=0),
           A.reveal_card(deck[1], player_id=0),
           A.win(2, 210)]
    )
    assert result.faults == {}


def test_R08_fresh_list_each_run():
    """R-08: each run gets a fresh actions list; the engine never changes a list after handing it over."""
    models = [Scripted(), Scripted()]
    result = play_hand(models, std_deck(), [500, 500])
    for m in models:
        assert all(m.kept[j] is not m.kept[k]
                   for j in range(len(m.kept)) for k in range(j))
        assert all(m.kept[j] is not result.actions for j in range(len(m.kept)))
        assert [list(x) for x in m.kept] == [s["actions"] for s in m.runs]


def test_R09_mutating_received_list_has_no_effect():
    """R-09: a model keeping or mutating its actions list changes nothing for the engine or later calls."""
    plain = [Scripted(), Scripted()]
    ref = play_hand(plain, std_deck(), [500, 500])
    mutating = [Scripted(mutate=True), Scripted()]
    got = play_hand(mutating, std_deck(), [500, 500])
    assert got.actions == ref.actions
    assert got.net == ref.net
    assert got.faults == ref.faults == {}
    assert [s["actions"] for s in mutating[0].runs] == [
        s["actions"] for s in plain[0].runs]
    assert [s["actions"] for s in mutating[1].runs] == [
        s["actions"] for s in plain[1].runs]


# -- faults ------------------------------------------------------------------

def test_F01_run_raises_nothing_owed_checks():
    """F-01: if run raises when nothing is owed, a check (call) is applied and the hand continues."""
    deck = std_deck()
    models = [Scripted(), Scripted([("exc", RuntimeError("x"))])]
    result = play_hand(models, deck, [500, 500])
    assert result.actions[:2] == [A.call(0), A.call(1)]
    assert set(result.faults) == {1}
    assert len(result.board) == 5
    check_result(result, deck, [500, 500])


def test_F02_run_raises_when_owing_folds():
    """F-02: if run raises when chips are owed, a fold is applied."""
    models = [Scripted([("raise", 60)]), Scripted([("exc", ValueError("x"))])]
    result = play_hand(models, std_deck(), [500, 500])
    assert result.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    assert result.net == [20, -20]
    assert set(result.faults) == {1}


@pytest.mark.parametrize("obj", [None, "call", 42, ("fold",)])
def test_F03_non_PlayerAction_replaced(obj):
    """F-03: a run returning a non-PlayerAction is replaced by check/fold and recorded."""
    models = [Scripted([("raise", 60)]), Scripted([("return", obj)])]
    result = play_hand(models, std_deck(), [500, 500])
    assert result.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    assert set(result.faults) == {1}


def test_F04_other_seats_id_replaced():
    """F-04: an action with another seat's id is replaced by a check/fold for the faulting seat."""
    models = [Scripted([("raise", 60)]), Scripted([("id", 0)])]
    result = play_hand(models, std_deck(), [500, 500])
    assert result.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    assert set(result.faults) == {1}


@pytest.mark.parametrize("first, bad", [
    (("raise", 60), ("raise", 25)),       # below min_raise_to (100)
    (("raise", 60), ("raise", 600)),      # above max_raise_to
    ("allin", ("raise", 500)),            # raising not allowed
])
def test_F05_illegal_raise_replaced(first, bad):
    """F-05: an out-of-range raise, or a raise when raising is not allowed, is replaced by check/fold."""
    models = [Scripted([first]), Scripted([bad])]
    result = play_hand(models, std_deck(), [500, 500])
    assert result.actions[1] == A.fold(1)
    assert result.net == [20, -20]
    assert set(result.faults) == {1}


def test_F06_seed_fault_never_asked_gets_replacements():
    """F-06: a seat whose seed raised is never run; every turn it checks (or folds), others play normally."""
    deck = std_deck()
    bad = SeedRaiser()
    good = Scripted()
    result = play_hand([good, bad], deck, [500, 500])
    assert bad.seed_calls == 1 and bad.run_calls == 0
    mine = [a for a in result.actions
            if a.player_id == 1 and a.kind not in ("reveal_card", "win")]
    assert len(mine) == 4 and all(a == A.call(1) for a in mine)
    assert set(result.faults) == {1}
    assert len(result.board) == 5
    check_result(result, deck, [500, 500])


def test_F06b_seed_fault_folds_when_owing():
    """F-06: a seed-faulted seat folds when chips are owed."""
    result = play_hand([Scripted([("raise", 60)]), SeedRaiser()], std_deck(),
                       [500, 500])
    assert result.actions == [A.raise_to(0, 60), A.fold(1), A.win(0, 80)]
    assert result.net == [20, -20]


def test_F07_seed_faulted_seat_can_win_showdown():
    """F-07: a seed-faulted seat that checks through to showdown can win the pot."""
    deck = hu_deck()
    result = play_hand([Scripted(), SeedRaiser()], deck, [500, 500])
    assert result.net == [-20, 20]


def test_F08_replacement_broadcast():
    """F-08: a replacement move is seen by other seats as that seat's move, and heads its own next actions list."""
    deck = std_deck()
    models = [Scripted(), Scripted([("exc", RuntimeError("x"))])]
    play_hand(models, deck, [500, 500])
    flop = [A.reveal_card(c) for c in deck[4:7]]
    assert models[1].runs[1]["actions"] == [A.call(1)] + flop
    assert models[0].runs[1]["actions"] == (
        [A.call(0), A.call(1)] + flop + [A.call(1)])


def test_F09_faults_dict_shape():
    """F-09: faults[seat] is a non-empty string; non-faulting seats are absent; a clean hand has {}."""
    clean = play_hand([Scripted(), Scripted()], std_deck(), [500, 500])
    assert clean.faults == {}
    bad = play_hand([Scripted(), Scripted([("return", None)])], std_deck(),
                    [500, 500])
    assert list(bad.faults) == [1]
    assert isinstance(bad.faults[1], str) and bad.faults[1]


def test_F10_first_fault_kept():
    """F-10: only a seat's first fault is recorded; later faults do not overwrite it."""
    first = ("exc", RuntimeError("first fault"))
    results = [
        play_hand([Scripted(), Scripted([first, second])], std_deck(),
                  [500, 500])
        for second in (("exc", TypeError("other")), ("return", None), "call")
    ]
    assert [r.actions for r in results[1:]] == [results[0].actions] * 2
    assert results[0].faults[1]
    assert results[0].faults == results[1].faults == results[2].faults


def test_F11_faults_never_stop_the_hand():
    """F-11: a hand where every model faults every turn still ends normally."""
    deck = std_deck()
    models = [Scripted(default=("exc", RuntimeError("x"))) for _ in range(3)]
    result = play_hand(models, deck, [500, 500, 500])
    assert result.actions == [A.fold(0), A.fold(1), A.win(2, 30)]
    assert result.net == [0, -10, 10]
    assert set(result.faults) == {0, 1}
    check_result(result, deck, [500, 500, 500])


def test_F12_seed_fault_recorded_even_if_never_acts():
    """F-12: a seed fault is recorded at seed time even if that seat never gets a turn."""
    models = [Scripted(["fold"]), Scripted(["fold"]), SeedRaiser()]
    result = play_hand(models, std_deck(), [500, 500, 500])
    assert result.net == [0, -10, 10]
    assert list(result.faults) == [2]
    assert isinstance(result.faults[2], str) and result.faults[2]

    all_bad = play_hand([SeedRaiser() for _ in range(3)], std_deck(),
                        [500, 500, 500])
    assert set(all_bad.faults) == {0, 1, 2}


def test_F13_base_exceptions_propagate():
    """F-13: KeyboardInterrupt from run and SystemExit from seed propagate (not faults)."""
    with pytest.raises(KeyboardInterrupt):
        play_hand([Scripted([("exc", KeyboardInterrupt())]), Scripted()],
                  std_deck(), [500, 500])
    with pytest.raises(SystemExit):
        play_hand([Scripted(), SeedRaiser(SystemExit(3))], std_deck(),
                  [500, 500])


# -- end to end --------------------------------------------------------------

def test_E01_heads_up_check_down():
    """E-01: heads-up check-down: the better hand nets +20, the worse -20."""
    deck = hu_deck()
    result = play_hand([Scripted(), Scripted()], deck, [500, 500])
    assert result.net == [-20, 20]
    assert result.faults == {}
    check_result(result, deck, [500, 500])


def test_E02_three_way_all_in_side_pots():
    """E-02: stacks 300/200/100 all-in preflop, shortest has AA, middle KK: net [-200, 0, +200]."""
    deck = deck_from("QS QH", "KS KH", "AS AH", board=NEUTRAL)
    models = [Scripted(["allin"]), Scripted(["call"]), Scripted(["call"])]
    result = play_hand(models, deck, [300, 200, 100])
    assert result.net == [-200, 0, 200]
    assert len(result.board) == 5
    assert [len(m.runs) for m in models] == [1, 1, 1]
    check_result(result, deck, [300, 200, 100])


def test_E03_split_pot_odd_chip():
    """E-03: blinds 5/10, SB folds, a board-plays tie splits 25 as 12/13 with the odd chip to seat 2."""
    deck = deck_from("2C 3D", "4C 5D", "6C 7D", board="AS KS QS JS TS")
    models = [Scripted(), Scripted(["fold"]), Scripted()]
    result = play_hand(models, deck, [500, 500, 500], small_blind=5,
                       big_blind=10)
    assert result.net == [2, -5, 3]
    check_result(result, deck, [500, 500, 500], small_blind=5, big_blind=10)


def test_E04_fold_before_flop():
    """E-04/H-02: SB folds preflop heads-up: empty board, no reveals, a single win of the pot, net [-10, +10]."""
    deck = std_deck()
    result = play_hand([Scripted(["fold"]), Scripted()], deck, [500, 500])
    assert result.board == []
    assert result.actions == [A.fold(0), A.win(1, 30)]
    assert result.net == [-10, 10]
    assert result.hands == [deck[0:2], deck[2:4]]


def test_E05_faulty_models_full_hand():
    """E-05: a 3-handed hand with a bad-return model and a seed-faulted model reaches showdown with valid results."""
    deck = std_deck()
    models = [Scripted(), Scripted(default=("return", None)), SeedRaiser()]
    result = play_hand(models, deck, [500, 500, 500])
    assert set(result.faults) == {1, 2}
    assert result.actions[:3] == [A.call(0), A.fold(1), A.call(2)]
    assert len(result.board) == 5
    check_result(result, deck, [500, 500, 500])


def test_E06_zero_stack_seat():
    """E-06: a zero-stack seat is dealt in and seeded, never asked to act, and nets 0."""
    deck = deck_from("AS AH", "7C 2D", "8S 3H", board=NEUTRAL)
    models = [Scripted() for _ in range(3)]
    result = play_hand(models, deck, [0, 500, 500])
    assert models[0].seeds == [(deck[0:2], 0)]
    assert models[0].runs == []
    assert result.net[0] == 0
    assert result.faults == {}
    check_result(result, deck, [0, 500, 500])

    hu = [Scripted(), Scripted()]
    r2 = play_hand(hu, std_deck(), [500, 0])
    assert hu[1].runs == [] and hu[0].runs == []
    assert r2.net == [0, 0]
    assert len(r2.board) == 5

import copy

from hypothesis import given, strategies as st

from engine import payouts
from card_utils import evaluate
from poker_test_helpers import cards, std_deck

NEUTRAL = cards("2C 7D 9H JC 4S")


def test_PAY01_lone_survivor_takes_pot():
    """PAY-01: one seat unfolded gets the whole pot; board/hands not looked at."""
    assert payouts([10, 20], [True, False], [[], []], [], 0) == [0, 30]
    hands = [cards("AS AH"), cards("2C 3D"), cards("KS KH")]
    assert payouts([20, 60, 40], [True, False, True], hands,
                   cards("5S 6S 7S"), 1) == [0, 120, 0]


def test_PAY02_best_hand_wins_folded_get_zero():
    """PAY-02: at showdown the higher evaluate() wins the whole pot; folded seats get 0."""
    hands = [cards("KS KH"), cards("AS AH"), cards("JD JH")]  # JJ folded (trips)
    assert payouts([50, 50, 20], [False, False, True], hands, NEUTRAL,
                   0) == [0, 120, 0]


def test_PAY03_tie_splits_evenly():
    """PAY-03: equal ranks split a pot evenly."""
    board = cards("AS KS QS JS TS")
    assert payouts([40, 40], [False, False], [cards("2C 3D"), cards("4C 5D")],
                   board, 0) == [40, 40]


def test_PAY04_odd_chip_goes_left_of_button():
    """PAY-04: an odd chip goes to the tied seat first in order button+1, button+2, ... mod n."""
    board = cards("AS KS QS JS TS")
    hands = [cards("2C 3D"), cards("4C 5D"), cards("6C 7D")]
    contributed = [10, 10, 5]
    folded = [False, False, True]
    assert payouts(contributed, folded, hands, board, 0) == [12, 13, 0]
    # order 2, 0, 1: seat 2 folded, so seat 0 (wraps past n-1)
    assert payouts(contributed, folded, hands, board, 1) == [13, 12, 0]
    # order 0, 1, 2
    assert payouts(contributed, folded, hands, board, 2) == [13, 12, 0]


def test_PAY05_three_way_tie_two_leftover_chips():
    """PAY-05: with pot % 3 == 2 in a three-way tie, the first two tied seats left of the button get one chip each."""
    board = cards("AS KS QS JS TS")
    hands = [cards("2C 3D"), cards("4C 5D"), cards("6C 7D"), cards("8C 9D")]
    contributed = [10, 10, 10, 2]
    folded = [False, False, False, True]
    # button 1: order 2, 3, 0, 1 -> seats 2 and 0
    assert payouts(contributed, folded, hands, board, 1) == [11, 10, 11, 0]


def test_PAY06_side_pots_three_all_in_levels():
    """PAY-06: contributions 300/200/100, shortest best: main 300 to shortest, side 200 to second, top seat gets its 100 back."""
    hands = [cards("QS QH"), cards("KS KH"), cards("AS AH")]
    assert payouts([300, 200, 100], [False, False, False], hands, NEUTRAL,
                   0) == [100, 200, 300]


def test_PAY07_folded_chips_count_but_folded_not_paid():
    """PAY-07: folded seats' chips fill each pot level, but folded seats are never paid."""
    hands = [cards("AS AH"), cards("KS KH"), cards("QS QH")]
    # folded 150 (best hand), all-in 50 (KK), caller 150 (QQ)
    assert payouts([150, 50, 150], [True, False, False], hands, NEUTRAL,
                   0) == [0, 150, 200]


def test_PAY08_ties_settled_per_pot():
    """PAY-08: ties and odd chips are settled per pot; a tied seat not eligible for a side pot gets none of it."""
    hands = [cards("AS KH"), cards("AD KC"), cards("3S 5H"), cards("8S 8H")]
    # levels 1, 24, 100: pots 4 (2/2), 69 (34/35, odd chip to seat 1),
    # 152 (seat 1 alone among the tied seats)
    assert payouts([24, 100, 100, 1], [False, False, False, True], hands,
                   NEUTRAL, 0) == [36, 189, 0, 0]


def test_PAY08b_odd_chip_per_pot_not_pooled():
    """PAY-08: odd chips are applied pot by pot, not to the pooled total."""
    hands = [cards("AS KH"), cards("AD KC"), cards("3S 5H"), cards("8S 8H"),
             cards("TS TH")]
    # levels 5, 6, 7, 10: pots 25, 4, 3, 6; pots 1 and 3 have an odd chip,
    # both going to seat 1 (first left of button 0)
    assert payouts([10, 10, 5, 6, 7], [False, False, False, True, True],
                   hands, NEUTRAL, 0) == [18, 20, 0, 0, 0]


def test_PAY09_pure():
    """PAY-09: payouts does not modify its arguments and is repeatable."""
    args = ([300, 200, 100], [False, False, False],
            [cards("QS QH"), cards("KS KH"), cards("AS AH")], list(NEUTRAL), 0)
    saved = copy.deepcopy(args)
    first = payouts(*args)
    assert args == saved
    assert payouts(*args) == first


@st.composite
def payout_inputs(draw):
    n = draw(st.integers(2, 6))
    deck = draw(st.permutations(std_deck()))
    hands = [list(deck[2 * i:2 * i + 2]) for i in range(n)]
    board = list(deck[2 * n:2 * n + 5])
    contributed = draw(st.lists(st.integers(0, 500), min_size=n, max_size=n))
    folded = draw(st.lists(st.booleans(), min_size=n, max_size=n))
    top = draw(st.integers(0, n - 1))
    folded[top] = False
    contributed[top] = max(contributed)
    button = draw(st.integers(0, n - 1))
    return contributed, folded, hands, board, button


@given(payout_inputs())
def test_PAYP1_conservation_nonneg_folded_zero(inputs):
    """PAY-P1: sum(payouts) == sum(contributed), every entry >= 0, folded seats get 0."""
    contributed, folded, hands, board, button = inputs
    result = payouts(contributed, folded, hands, board, button)
    assert len(result) == len(contributed)
    assert sum(result) == sum(contributed)
    assert all(x >= 0 for x in result)
    assert all(result[i] == 0 for i in range(len(folded)) if folded[i])


@given(payout_inputs())
def test_PAYP2_matched_amount_bound(inputs):
    """PAY-P2: no seat gets more than sum_j min(c_j, c_i); a strictly best unfolded seat gets exactly that."""
    contributed, folded, hands, board, button = inputs
    result = payouts(contributed, folded, hands, board, button)
    n = len(contributed)
    matched = [sum(min(c, contributed[i]) for c in contributed)
               for i in range(n)]
    assert all(result[i] <= matched[i] for i in range(n))
    alive = [i for i in range(n) if not folded[i]]
    ranks = {i: evaluate(hands[i] + board) for i in alive}
    best = max(ranks.values())
    winners = [i for i in alive if ranks[i] == best]
    if len(winners) == 1:
        assert result[winners[0]] == matched[winners[0]]

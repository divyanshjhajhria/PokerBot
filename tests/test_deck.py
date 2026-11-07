from card_utils import RANKS, SUITS, Card, full_deck


def test_D01_52_distinct_cards_every_combination():
    """D-01: full_deck() returns 52 distinct Cards, every rank/suit once."""
    deck = full_deck()
    assert len(deck) == 52
    assert all(isinstance(c, Card) for c in deck)
    assert len(set(deck)) == 52
    assert {(c.rank, c.suit) for c in deck} == {
        (r, s) for r in RANKS for s in SUITS
    }


def test_D02_fixed_order():
    """D-02: full_deck() is ordered by rank (lowest first), then suit."""
    assert full_deck() == [Card(r, s) for r in RANKS for s in SUITS]


def test_D03_new_list_each_call():
    """D-03: each call returns a new list; mutating one does not affect the next."""
    first = full_deck()
    first.reverse()
    first.pop()
    second = full_deck()
    assert second is not first
    assert second == [Card(r, s) for r in RANKS for s in SUITS]

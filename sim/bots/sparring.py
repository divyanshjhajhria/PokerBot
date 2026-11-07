"""Sparring bots that imitate what other teams are likely to submit."""
import random
from card_utils import evaluate, full_deck
from player_action import PlayerAction


class _B:
    def __init__(self):
        self.hand = []; self.player_id = None
    def seed(self, hand, player_id):
        self.hand = hand; self.player_id = player_id
    def _raise(self, gs, total):
        if not gs.can_raise: return PlayerAction.call(self.player_id)
        lo, hi = gs.raise_range
        return PlayerAction.raise_to(self.player_id, min(hi, max(lo, int(total))))
    def _cf(self, gs):
        return PlayerAction.call(self.player_id) if gs.call_amount == 0 else PlayerAction.fold(self.player_id)


class ExampleBot(_B):
    """The CLAUDE.md example bot verbatim: likely the most common submission."""
    def run(self, actions, gs):
        me = self.player_id
        if len(gs.board) >= 3:
            strong = evaluate(self.hand + list(gs.board))[0] >= 2
        else:
            high, low = sorted(c.value for c in self.hand)[::-1]
            strong = high == low or low >= 10
        if strong and "raise" in gs.legal_actions:
            lo, hi = gs.raise_range
            return PlayerAction.raise_to(me, min(hi, max(lo, gs.pot)))
        if gs.call_amount == 0 or gs.pot_odds < 0.25:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)


class MinRaiseBot(_B):
    """Min-raises at every opportunity, otherwise calls."""
    def run(self, actions, gs):
        if gs.can_raise: return self._raise(gs, gs.min_raise_to)
        return PlayerAction.call(self.player_id)


class JamOverRaiseBot(_B):
    """Limps/checks/calls, but shoves whenever it faces a raise with a
    'playable' hand (any pair, ace, or two broadways); folds junk to raises."""
    def run(self, actions, gs):
        high, low = sorted(c.value for c in self.hand)[::-1]
        playable = high == low or high == 14 or low >= 10
        if gs.street == "preflop" and gs.current_bet > gs.big_blind and gs.call_amount > 0:
            if playable: return self._raise(gs, gs.max_raise_to)
            return PlayerAction.fold(self.player_id)
        return PlayerAction.call(self.player_id)


class TAGBot(_B):
    """A reasonable hand-crafted tight-aggressive heads-up bot."""
    PRE = None
    def run(self, actions, gs):
        me = self.player_id
        high, low = sorted(c.value for c in self.hand)[::-1]
        suited = self.hand[0].suit == self.hand[1].suit
        pair = high == low
        score = (high + low) / 28 + (0.08 if suited else 0) + (0.35 if pair else 0) + (0.1 if high - low <= 2 else 0)
        if gs.street == "preflop":
            if gs.current_bet > gs.big_blind and gs.call_amount > 0:
                if pair and high >= 9 or (high == 14 and low >= 11): return self._raise(gs, gs.max_raise_to)
                if score >= 0.75 and gs.pot_odds < 0.4: return PlayerAction.call(me)
                return PlayerAction.fold(me)
            if score >= 0.95: return self._raise(gs, 3 * gs.big_blind)
            if score >= 0.62: return self._raise(gs, 2.5 * gs.big_blind)
            return self._cf(gs)
        board = list(gs.board)
        rank = evaluate(self.hand + board)
        cat = rank[0]
        board_ranks = {c.rank for c in board}
        hit = pair or any(c.rank in board_ranks for c in self.hand)
        top = max(c.value for c in board)
        strong = cat >= 2 and hit
        toppair = cat == 1 and hit and (high >= top or pair and high >= top)
        if gs.call_amount == 0:
            if strong: return self._raise(gs, gs.bets[me] + gs.pot * 0.8)
            if toppair: return self._raise(gs, gs.bets[me] + gs.pot * 0.5)
            return PlayerAction.call(me)
        if strong: return self._raise(gs, gs.current_bet * 3)
        if toppair and gs.pot_odds < 0.4: return PlayerAction.call(me)
        if cat >= 1 and hit and gs.pot_odds < 0.25: return PlayerAction.call(me)
        return PlayerAction.fold(me)


class LimpShoveBot(_B):
    """Limps every hand preflop; shoves any flop it pairs or better, else check/folds."""
    def run(self, actions, gs):
        if gs.street == "preflop":
            if gs.current_bet > gs.big_blind and gs.call_amount > 0:
                return PlayerAction.fold(self.player_id) if gs.pot_odds > 0.35 else PlayerAction.call(self.player_id)
            return PlayerAction.call(self.player_id)
        cat = evaluate(self.hand + list(gs.board))[0]
        if cat >= 1: return self._raise(gs, gs.max_raise_to)
        return self._cf(gs)


# -- added 15:00: a chart-based "LLM-GTO" bot and three ExampleBot variants --

def _mc_equity_vs_random(rng, hand, board, samples=100):
    """Share of the pot won vs one random hand (ties split), card_utils.evaluate."""
    unseen = [c for c in full_deck() if c not in hand and c not in board]
    missing = 5 - len(board)
    won = 0.0
    for _ in range(samples):
        cards = rng.sample(unseen, missing + 2)
        full = list(board) + cards[:missing]
        mine, theirs = evaluate(list(hand) + full), evaluate(cards[missing:] + full)
        won += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
    return won / samples


class LLMGTOBot(_B):
    """What an LLM tends to write when asked for a 'GTO-ish' bot: open the
    top 60% to 2.5x, 3-bet jam the top 12%, c-bet 65% pot on every flop as
    the preflop raiser, otherwise call by pot odds vs 100-sample equity
    against a random hand."""

    def __init__(self, seed=None):
        super().__init__()
        self.rng = random.Random(seed)
        self.raised_pre = False

    def seed(self, hand, player_id):
        super().seed(hand, player_id)
        self.raised_pre = False

    def _pct(self):
        from preflop_tables import PCT
        a, b = sorted(self.hand, key=lambda c: -c.value)
        key = a.rank + b.rank if a.rank == b.rank else a.rank + b.rank + ('s' if a.suit == b.suit else 'o')
        return PCT[key]

    def run(self, actions, gs):
        me = self.player_id
        if gs.street == 'preflop':
            pct = self._pct()
            if gs.current_bet > gs.big_blind and gs.call_amount > 0:      # facing a raise
                if pct <= 0.12 and gs.can_raise:
                    self.raised_pre = True
                    return self._raise(gs, gs.max_raise_to)
                eq = _mc_equity_vs_random(self.rng, self.hand, [])
                return PlayerAction.call(me) if eq > gs.pot_odds else PlayerAction.fold(me)
            if pct <= 0.60 and gs.can_raise:                               # open / iso
                self.raised_pre = True
                return self._raise(gs, 2.5 * gs.big_blind)
            return self._cf(gs)
        board = list(gs.board)
        if gs.call_amount == 0:
            if gs.street == 'flop' and self.raised_pre and gs.can_raise:
                return self._raise(gs, gs.bets[me] + 0.65 * gs.pot)
            if _mc_equity_vs_random(self.rng, self.hand, board) > 0.75 and gs.can_raise:
                return self._raise(gs, gs.bets[me] + 0.65 * gs.pot)
            return PlayerAction.call(me)
        eq = _mc_equity_vs_random(self.rng, self.hand, board)
        return PlayerAction.call(me) if eq > gs.pot_odds else PlayerAction.fold(me)


class _ExampleVariant(_B):
    """ExampleBot with other thresholds and sizes."""
    PAIR_MIN, LOW_MIN, ACE_LOW, MULT, CALL_ODDS, POST_CAT = 2, 10, None, 2, 0.33, 2

    def run(self, actions, gs):
        me = self.player_id
        if len(gs.board) >= 3:
            strong = evaluate(self.hand + list(gs.board))[0] >= self.POST_CAT
        else:
            high, low = sorted(c.value for c in self.hand)[::-1]
            strong = ((high == low and high >= self.PAIR_MIN) or low >= self.LOW_MIN
                      or (self.ACE_LOW is not None and high == 14 and low >= self.ACE_LOW))
        if strong and gs.can_raise:
            if gs.street == 'preflop':
                target = self.MULT * max(gs.current_bet, gs.big_blind)
            else:
                target = max(gs.pot, self.MULT * gs.current_bet)
            return self._raise(gs, target)
        if gs.call_amount == 0 or gs.pot_odds < self.CALL_ODDS:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)


class ExampleV1(_ExampleVariant):
    """Example thresholds, 2x raises, calls when pot odds < 0.33."""


class ExampleV2(_ExampleVariant):
    """Looser: pairs 5+, both cards 9+, A7+; 3x raises; calls < 0.33."""
    PAIR_MIN, LOW_MIN, ACE_LOW, MULT = 5, 9, 7, 3


class ExampleV3(_ExampleVariant):
    """Tighter preflop (pairs 7+, both J+), raises any made pair postflop,
    3x; calls only when pot odds < 0.25."""
    PAIR_MIN, LOW_MIN, MULT, CALL_ODDS, POST_CAT = 7, 11, 3, 0.25, 1


class AntiAIBot(_B):
    """Built to punish equity-and-pot-odds bots: opens 100% from the SB to
    2.5x; facing any raise, jams its top 20% and folds the rest; c-bets
    every flop at 75% pot; overbets 1.5x pot on the turn/river with any hand
    40% of the time; folds to bets over 60% pot without top pair or better;
    calls smaller bets with anything."""

    def __init__(self, seed=None):
        super().__init__()
        self.rng = random.Random(seed)
        self.raised_pre = False

    def seed(self, hand, player_id):
        super().seed(hand, player_id)
        self.raised_pre = False

    def _pct(self):
        from preflop_tables import PCT
        a, b = sorted(self.hand, key=lambda c: -c.value)
        key = a.rank + b.rank if a.rank == b.rank else a.rank + b.rank + ('s' if a.suit == b.suit else 'o')
        return PCT[key]

    def _top_pair_plus(self, board):
        cat = evaluate(self.hand + board)[0]
        if cat >= 2:
            return True
        if cat == 1:
            top = max(c.value for c in board)
            hole = [c.value for c in self.hand]
            if hole[0] == hole[1] and hole[0] >= top:      # overpair (or set handled above)
                return True
            return any(v == top for v in hole)               # top pair with a hole card
        return False

    def run(self, actions, gs):
        me = self.player_id
        if gs.street == 'preflop':
            if gs.current_bet > gs.big_blind and gs.call_amount > 0:     # facing any raise
                if self._pct() <= 0.20:
                    return self._raise(gs, gs.max_raise_to)           # jam (calls if it cannot raise)
                return PlayerAction.fold(me)
            if gs.call_amount > 0 and gs.can_raise:                     # SB first in: open 100%
                self.raised_pre = True
                return self._raise(gs, 2.5 * gs.big_blind)
            return PlayerAction.call(me)                               # BB vs a limp: check
        board = list(gs.board)
        if gs.call_amount == 0:
            if gs.street == 'flop' and self.raised_pre and gs.can_raise:
                return self._raise(gs, gs.bets[me] + 0.75 * gs.pot)
            if gs.street in ('turn', 'river') and gs.can_raise and self.rng.random() < 0.40:
                return self._raise(gs, gs.bets[me] + 1.5 * gs.pot)
            return PlayerAction.call(me)
        bet = gs.call_amount
        if bet > 0.60 * max(1, gs.pot - bet) and not self._top_pair_plus(board):
            return PlayerAction.fold(me)
        return PlayerAction.call(me)

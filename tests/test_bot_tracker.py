"""Hand replay -> profile counts, checked on hand-built hands and live dummies."""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OURS = os.path.join(ROOT, 'sim', 'ours')  # our frozen bot
sys.path.insert(0, ROOT)
sys.path.insert(0, OURS)

try:
    from arena.dummies import DUMMIES
    from card_utils import Card, full_deck
    from engine import play_hand
    from player_action import PlayerAction as A
    import ours_model as model
    import tracker
except ImportError:  # needs the engine venv (pydantic)
    model = None


def C(s):
    return Card.parse(s[0] + s[1].upper())


def deal(*cards):
    return [A.reveal_card(C(c)) for c in cards]


@unittest.skipIf(model is None, 'needs the engine venv (.venv/bin/python)')
class TestHandEvents(unittest.TestCase):
    # Heads-up, button 0 = small blind, blinds 10/20, 500 each.
    def ev(self, actions, me):
        return tracker.hand_events(actions, me, [500, 500], 0, 10, 20, ['a', 'b'])

    def test_open_shove(self):
        ev = self.ev([A.raise_to(0, 500), A.fold(1), A.win(0, 40)], me=1)[0]
        self.assertEqual(ev, {'pre_opp': 1, 'first_in': 1, 'open_raise': 1, 'open_shove': 1,
                              'vpip': 1, 'pfr': 1, 'shoves': 1, 'raises': 1})

    def test_cbet_fold(self):
        acts = ([A.raise_to(0, 60), A.call(1)] + deal('2c', '7d', 'Kh')
                + [A.call(1), A.raise_to(0, 60), A.fold(1), A.win(0, 180)])
        ev = self.ev(acts, me=0)[1]
        self.assertEqual(ev, {'pre_opp': 1, 'face_raise': 1, 'vpip': 1, 'saw_flop': 1,
                              'post_checks': 1, 'face_bet': 1, 'fold_to_bet': 1,
                              'face_cbet': 1, 'fold_to_cbet': 1, 'face_big': 1, 'fold_big': 1})

    def test_river_bluff_shown(self):
        acts = ([A.call(0), A.call(1)] + deal('As', 'Kd', 'Qh')
                + [A.call(1), A.call(0)] + deal('9c') + [A.call(1), A.call(0)] + deal('3s')
                + [A.call(1), A.raise_to(0, 480), A.call(1),
                   A.reveal_card(C('Ah'), 1), A.reveal_card(C('5h'), 1),
                   A.reveal_card(C('7c'), 0), A.reveal_card(C('2d'), 0), A.win(1, 1000)])
        ev = self.ev(acts, me=1)[0]
        self.assertEqual(ev['open_limp'], 1)
        self.assertEqual(ev['vpip'], 1)
        self.assertEqual(ev['post_checks'], 2)
        self.assertEqual(ev['post_aggr'], 1)
        self.assertEqual((ev['showdown'], ev['sd_aggr'], ev['sd_bluff']), (1, 1, 1))
        self.assertNotIn('shoves', ev)   # postflop shoves are not preflop shoves

    def test_weak_showdown(self):
        board = [C(x) for x in ('As', 'Kd', 'Qh', '9c', '3s')]
        self.assertTrue(tracker.weak_showdown([C('7c'), C('2d')], board))     # high card
        self.assertTrue(tracker.weak_showdown([C('7c'), C('2d')],
                                              [C(x) for x in ('As', 'Ad', 'Qh', '9c', '3s')]))
        self.assertFalse(tracker.weak_showdown([C('Ah'), C('2d')], board))    # top pair
        self.assertFalse(tracker.weak_showdown([C('Jh'), C('Td')], board))    # straight


def play(m, opp, name, hands):
    faults = 0
    for d in range(hands):
        deck = full_deck()
        random.Random(d).shuffle(deck)
        seats, teams = ([m, opp], ['us', name]) if d % 2 == 0 else ([opp, m], [name, 'us'])
        faults += len(play_hand(seats, deck, [500, 500], button=0, teams=teams).faults)
    return faults


@unittest.skipIf(model is None, 'needs the engine venv (.venv/bin/python)')
class TestLabelsVsDummies(unittest.TestCase):
    def fast_model(self):
        m = model.Model()
        m.core.cfg['mc_iters'] = {1: 500}
        return m

    def check(self, name, hands, expected):
        m = self.fast_model()
        faults = play(m, DUMMIES[name](seed=1), name, hands)
        self.assertEqual(faults, 0)
        self.assertEqual(m.core.stats['errors'], 0, m.core.stats['last_error'])
        self.assertEqual(m.core.profile(name).label(), expected)
        return m.core.profile(name)

    def test_shove_is_maniac_within_5_hands(self):
        p = self.check('shove', 5, 'maniac')
        self.assertEqual(p.range_frac('shove'), 1.0)

    def test_fold_is_nit(self):
        self.check('fold', 40, 'nit')

    def test_call_is_station(self):
        self.check('call', 80, 'station')

    def test_random_is_random(self):
        self.check('random', 80, 'random')

    def test_end_game_never_raises_on_garbage(self):
        from game_state import GameState, GameStateView
        m = self.fast_model()
        m.seed([C('As'), C('Kd')], 0)
        view = GameStateView(GameState([500, 500]))
        m.end_game([A.raise_to(1, 50)] * 3, view)      # out of turn: replay fails
        self.assertEqual(m.core.stats['errors'], 1)
        self.assertEqual(m.hand_actions, [])
        self.assertEqual(m.core.profiles, {})


if __name__ == '__main__':
    unittest.main()

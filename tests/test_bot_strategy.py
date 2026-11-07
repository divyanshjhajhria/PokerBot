"""Key heads-up spots for strategy.PokerBot (engine-free)."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim', 'ours'))  # our frozen bot
import bot  # noqa: E402
import strategy  # noqa: E402
from bot import CALL, CHECK, FOLD, RAISE, Legal  # noqa: E402

MANIAC = dict(hands=10, pre_opp=10, vpip=10, pfr=10, shoves=10, shoves_first5=5,
              first_in=5, open_raise=5, open_shove=5, face_raise=5, three_bet=5,
              shove_over_raise=5)
NIT = dict(hands=60, pre_opp=60, vpip=1, first_in=30, open_fold=29, face_raise=30,
           fold_vs_raise=29, face_bet=10, fold_to_bet=9, post_checks=30, post_aggr=1)
STATION = dict(hands=60, pre_opp=60, vpip=55, first_in=30, open_limp=29, face_raise=30,
               fold_vs_raise=1, face_bet=40, fold_to_bet=2, face_shove=10,
               call_vs_shove=9, post_calls=40)


def make_bot(profile=None, **cfg):
    b = strategy.PokerBot(config=cfg)
    if profile:
        b.profile('villain').c.update(profile)
    return b


def st(hole, board='', pot=30, to_call=10, my_bet=10, stack=490, opp_bet=20,
       opp_stack=480, button=True, line=(), start_eff=500):
    return {'hole': hole.split(), 'board': board.split(), 'pot': pot, 'to_call': to_call,
            'stack': stack, 'my_bet': my_bet,
            'min_raise_to': min(my_bet + stack, max(opp_bet, my_bet) + max(20, to_call)),
            'max_raise_to': my_bet + stack, 'can_raise': opp_stack > 0 and stack > to_call,
            'big_blind': 20, 'num_opponents': 1, 'opponents': ['villain'],
            'opp_bet': opp_bet, 'opp_stack': opp_stack, 'is_button': button,
            'line': list(line), 'start_eff': start_eff}


def act(b, state):
    out = b.act(state)
    assert b.stats['errors'] == 0, b.stats['last_error']
    return out['action'], out['amount']


# BB facing an SB open-shove of 500 (25 bb).
JAM = dict(pot=520, to_call=480, my_bet=20, stack=480, opp_bet=500, opp_stack=0,
           button=False, line=[('preflop', 'opp', 'shove')])


class TestPreflop(unittest.TestCase):
    def test_aces_never_fold_first_in(self):
        self.assertEqual(act(make_bot(), st('As Ad'))[0], RAISE)

    def test_steal_any_two_vs_nit(self):
        kind, amt = act(make_bot(NIT), st('7c 2d'))
        self.assertEqual((kind, amt), (RAISE, 50))   # not a jammer: standard size

    def test_fold_trash_vs_maniac_behind(self):
        self.assertEqual(act(make_bot(MANIAC), st('7c 2d'))[0], FOLD)

    def test_premium_vs_maniac_goes_in(self):
        self.assertEqual(act(make_bot(MANIAC), st('Ah Kd'))[0], RAISE)

    def test_call_maniac_jam_by_equity(self):
        b = make_bot(MANIAC)
        for hole, want in (('4c 4d', CALL), ('Kh 9d', CALL), ('7c 2d', FOLD), ('Jc 3d', FOLD)):
            self.assertEqual(act(b, st(hole, **JAM))[0], want, hole)

    def test_spec_jam_range_at_7pct_premium(self):
        b = make_bot(MANIAC, risk_premium=0.07)
        for hole in ('4c 4d', 'Ah 2h', 'Ah 7d', 'Kh 9h', 'Kh Td', 'Qh Th', 'Jh Th'):
            self.assertEqual(act(b, st(hole, **JAM))[0], CALL, hole)
        for hole in ('3c 3d', 'Th 9h', '7c 2d'):
            self.assertEqual(act(b, st(hole, **JAM))[0], FOLD, hole)

    def test_fold_to_nit_jam(self):
        self.assertEqual(act(make_bot(NIT), st('Ah Jd', **JAM))[0], FOLD)

    def test_pushfold_uses_nash_with_no_data(self):
        short = dict(stack=150, opp_stack=140, start_eff=160)
        self.assertEqual(act(make_bot(), st('Kc 2d', **short)), (RAISE, 160))


class TestPostflop(unittest.TestCase):
    def test_call_maniac_flop_jam_with_top_pair(self):
        s = st('As 9d', 'Ah 7c 2s', pot=550, to_call=450, my_bet=0, stack=450, opp_bet=450,
               opp_stack=0, button=False,
               line=[('preflop', 'opp', 'raise'), ('preflop', 'me', 'call'),
                     ('flop', 'me', 'check'), ('flop', 'opp', 'shove')])
        self.assertEqual(act(make_bot(MANIAC), s)[0], CALL)

    def test_fold_small_pair_to_nit_bet(self):
        s = st('3c 3d', 'Kh Qs 9d', pot=200, to_call=100, my_bet=0, stack=400, opp_bet=100,
               opp_stack=300, button=False,
               line=[('preflop', 'opp', 'raise'), ('preflop', 'me', 'call'),
                     ('flop', 'me', 'check'), ('flop', 'opp', 'raise')])
        self.assertEqual(act(make_bot(NIT), s)[0], FOLD)

    def test_bet_the_nuts(self):
        s = st('Ah Kh', 'Qh Jh Th 2c 3d', pot=200, to_call=0, my_bet=0, stack=400, opp_bet=0,
               opp_stack=400, button=True,
               line=[('preflop', 'me', 'raise'), ('preflop', 'opp', 'call')]
               + [(x, 'opp', 'check') for x in ('flop', 'turn', 'river')])
        self.assertEqual(act(make_bot(STATION), s)[0], RAISE)

    def test_never_bluff_a_station(self):
        s = st('7c 2d', 'Ah Kd 9s 5c 3h', pot=200, to_call=0, my_bet=0, stack=400, opp_bet=0,
               opp_stack=400, button=True,
               line=[('preflop', 'me', 'raise'), ('preflop', 'opp', 'call'),
                     ('river', 'opp', 'check')])
        self.assertEqual(act(make_bot(STATION), s)[0], CHECK)

    def test_bluff_a_nit_on_dry_board(self):
        s = st('7c 2d', 'Ah 8d 3s', pot=100, to_call=0, my_bet=0, stack=450, opp_bet=0,
               opp_stack=450, button=True,
               line=[('preflop', 'me', 'raise'), ('preflop', 'opp', 'call'),
                     ('flop', 'opp', 'check')])
        self.assertEqual(act(make_bot(NIT), s)[0], RAISE)


class TestRobustness(unittest.TestCase):
    def test_random_hu_states_are_legal_and_error_free(self):
        rng = random.Random(4)
        kinds = ['fold', 'check', 'call', 'raise', 'shove']
        for profile in (None, MANIAC, NIT, STATION):
            b = make_bot(profile, post_iters=300)
            for _ in range(150):
                cards = rng.sample(range(52), 7)
                nb = rng.choice([0, 0, 3, 4, 5])
                stack = rng.choice([0, 20, 150, 480])
                to_call = rng.choice([0, 10, 40, 200, 480])
                opp_stack = rng.choice([0, 100, 480])
                line = [(rng.choice(['preflop', 'flop', 'turn', 'river']),
                         rng.choice(['me', 'opp']), rng.choice(kinds))
                        for _ in range(rng.randint(0, 5))]
                s = st(' '.join(bot.card_str(c) for c in cards[:2]),
                       ' '.join(bot.card_str(c) for c in cards[2:2 + nb]),
                       pot=rng.choice([30, 100, 600]), to_call=to_call,
                       my_bet=rng.choice([0, 10, 20]), stack=stack,
                       opp_bet=rng.choice([0, 20, 200]), opp_stack=opp_stack,
                       button=rng.random() < 0.5, line=line,
                       start_eff=rng.choice([None, 100, 500]))
                if stack == 0 and to_call > 0:
                    continue
                out = b.act(s)
                L = Legal.from_state(s)
                k, amt = out['action'], out['amount']
                self.assertTrue(k != FOLD or not L.can_check)
                self.assertTrue(k != RAISE or (L.can_raise and amt <= L.max_raise_to))
            self.assertEqual(b.stats['errors'], 0, b.stats['last_error'])


if __name__ == '__main__':
    unittest.main()

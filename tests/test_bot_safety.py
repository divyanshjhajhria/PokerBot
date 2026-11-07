import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim', 'ours'))  # our frozen bot
import bot  # noqa: E402
from bot import CALL, CHECK, FOLD, RAISE, Legal, sanitize  # noqa: E402


def L(**kw):
    kw.setdefault('bb', 20)
    return Legal(**kw)


def assert_legal(tc, out, legal):
    kind, amt = out
    if kind == FOLD:
        tc.assertFalse(legal.can_check, 'folded when check was free')
    elif kind == CHECK:
        tc.assertTrue(legal.can_check, 'checked facing a bet')
    elif kind == CALL:
        tc.assertGreater(legal.to_call, 0)
        tc.assertEqual(amt, min(legal.to_call, legal.stack))
    elif kind == RAISE:
        tc.assertTrue(legal.can_raise)
        tc.assertLessEqual(amt, legal.max_raise_to)
        tc.assertGreater(amt, legal.my_bet + legal.to_call)
        tc.assertTrue(amt >= legal.min_raise_to or amt == legal.max_raise_to,
                      'raise %d below min %d and not all-in' % (amt, legal.min_raise_to))
    else:
        tc.fail('unknown action %r' % (kind,))


class TestSanitize(unittest.TestCase):
    def test_fold_when_check_free_becomes_check(self):
        self.assertEqual(sanitize((FOLD, 0), L(to_call=0, stack=1000)), (CHECK, 0))

    def test_check_facing_bet_becomes_fold(self):
        self.assertEqual(sanitize((CHECK, 0), L(to_call=50, stack=1000)), (FOLD, 0))

    def test_call_nothing_is_check(self):
        self.assertEqual(sanitize((CALL, 0), L(to_call=0, stack=1000)), (CHECK, 0))

    def test_call_more_than_stack_is_allin_call(self):
        self.assertEqual(sanitize((CALL, 500), L(to_call=500, stack=300)), (CALL, 300))

    def test_raise_below_min_clamped_up(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40)
        self.assertEqual(sanitize((RAISE, 25), lg), (RAISE, 40))

    def test_raise_above_stack_is_allin(self):
        lg = L(to_call=20, stack=1000, my_bet=10, min_raise_to=40)
        self.assertEqual(sanitize((RAISE, 5000), lg), (RAISE, 1010))

    def test_allin_keywords(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40)
        for k in ('allin', 'all_in', 'all-in', 'shove', 'jam'):
            self.assertEqual(sanitize((k,), lg), (RAISE, 1000))

    def test_short_stack_allin_for_less_than_min_raise(self):
        # 30 behind, facing 20, min raise-to 40: only all-in (30) is a raise.
        lg = L(to_call=20, stack=30, min_raise_to=40)
        self.assertEqual(sanitize((RAISE, 40), lg), (RAISE, 30))

    def test_allin_for_less_disabled_falls_back_to_call(self):
        old = bot.CONFIG['allin_for_less_ok']
        bot.CONFIG['allin_for_less_ok'] = False
        try:
            lg = L(to_call=20, stack=30, min_raise_to=40)
            self.assertEqual(sanitize((RAISE, 40), lg), (CALL, 20))
        finally:
            bot.CONFIG['allin_for_less_ok'] = old

    def test_cannot_raise_when_stack_covers_only_call(self):
        lg = L(to_call=20, stack=20)
        self.assertFalse(lg.can_raise)
        self.assertEqual(sanitize((RAISE, 100), lg), (CALL, 20))

    def test_cannot_raise_when_stack_below_call(self):
        lg = L(to_call=200, stack=50, my_bet=100)
        self.assertEqual(sanitize((RAISE, 1000), lg), (CALL, 50))

    def test_action_not_reopened(self):
        # e.g. an all-in for less than a full raise does not reopen betting
        lg = L(to_call=30, stack=1000, can_raise=False)
        self.assertEqual(sanitize((RAISE, 200), lg), (CALL, 30))

    def test_bet_unopened_pot_default_min_is_bb(self):
        lg = L(to_call=0, stack=1000)
        self.assertEqual(lg.min_raise_to, 20)
        self.assertEqual(sanitize(('bet', 10), lg), (RAISE, 20))

    def test_snap_allin_when_tiny_leftover(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40)
        self.assertEqual(sanitize((RAISE, 990), lg), (RAISE, 1000))

    def test_engine_max_raise_respected(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40, max_raise_to=500)
        self.assertEqual(sanitize((RAISE, 800), lg), (RAISE, 500))

    def test_rounding_and_negative(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40)
        self.assertEqual(sanitize((RAISE, 45.6), lg), (RAISE, 46))
        self.assertEqual(sanitize((RAISE, -5), lg), (RAISE, 40))

    def test_garbage_inputs_fall_back(self):
        free, facing = L(to_call=0, stack=1000), L(to_call=50, stack=1000)
        for junk in (None, 'banana', (RAISE, float('nan')), (RAISE, 'x'), (), 42):
            self.assertEqual(sanitize(junk, free), (CHECK, 0), junk)
            self.assertEqual(sanitize(junk, facing), (FOLD, 0), junk)

    def test_dict_input(self):
        lg = L(to_call=20, stack=1000, min_raise_to=40)
        self.assertEqual(sanitize({'action': 'raise', 'amount': 100}, lg), (RAISE, 100))
        self.assertEqual(sanitize({'action': 'CALL'}, lg), (CALL, 20))

    def test_fuzz_always_legal(self):
        old = bot.CONFIG['allin_for_less_ok']
        try:
            for flag in (True, False):
                bot.CONFIG['allin_for_less_ok'] = flag
                self._fuzz(flag)
        finally:
            bot.CONFIG['allin_for_less_ok'] = old

    def _fuzz(self, allin_for_less_ok):
        rng = random.Random(7)
        kinds = [FOLD, CHECK, CALL, RAISE, 'bet', 'allin', 'weird', None]
        for _ in range(20000):
            stack = rng.choice([0, 1, 5, 19, 20, 21, 39, 40, 100, 1000, 5000])
            to_call = rng.choice([0, 0, 1, 10, 20, 40, 100, 999, 3000])
            my_bet = rng.choice([0, 0, 10, 20, 100])
            mrt = rng.choice([None, my_bet + to_call + rng.choice([1, 20, 40, 500])])
            cr = rng.choice([None, True, False])
            lg = L(to_call=to_call, stack=stack, my_bet=my_bet, min_raise_to=mrt, can_raise=cr)
            if lg.stack == 0 and lg.to_call > 0:
                continue  # engine never asks an all-in player to act
            prop = (rng.choice(kinds), rng.choice([-10, 0, 1, 20, 41, 55.5, 1e9, float('nan')]))
            out = sanitize(prop, lg)
            assert_legal(self, out, lg)
            if out[0] == RAISE and not allin_for_less_ok:
                self.assertGreaterEqual(out[1], lg.min_raise_to)


class BoomBot(bot.Bot):
    def _decide(self, state, legal, deadline):
        raise RuntimeError('boom')


class SlowBot(bot.Bot):
    def _decide(self, state, legal, deadline):
        time.sleep(0.07)
        return (RAISE, 10 ** 6)


STATE = {'hole': ['As', 'Ad'], 'board': [], 'pot': 30, 'to_call': 20,
         'stack': 1000, 'big_blind': 20, 'num_opponents': 1}


class TestGuard(unittest.TestCase):
    def test_exception_falls_back(self):
        b = BoomBot()
        self.assertEqual(b.act(dict(STATE)), {'action': FOLD, 'amount': 0})
        self.assertEqual(b.act(dict(STATE, to_call=0)), {'action': CHECK, 'amount': 0})
        self.assertEqual(b.stats['errors'], 2)
        self.assertIn('boom', b.stats['last_error'])

    def test_slow_decision_falls_back(self):
        b = SlowBot(config={'time_limit': 0.1})  # guard at 60 ms
        self.assertEqual(b.act(dict(STATE, to_call=0)), {'action': CHECK, 'amount': 0})
        self.assertEqual(b.stats['timeouts'], 1)

    def test_malformed_state(self):
        b = bot.Bot()
        self.assertEqual(b.act({'to_call': 0}), {'action': CHECK, 'amount': 0})
        self.assertEqual(b.act({'to_call': 10}), {'action': FOLD, 'amount': 0})
        self.assertEqual(b.act(None), {'action': FOLD, 'amount': 0})

    def test_bad_cards_fall_back(self):
        b = bot.Bot()
        self.assertEqual(b.act(dict(STATE, hole=['Zz', 'Ad'])), {'action': FOLD, 'amount': 0})

    def test_real_decisions_are_legal(self):
        b = bot.Bot(seed=3)
        rng = random.Random(11)
        for _ in range(300):
            cards = rng.sample(range(52), 7)
            nb = rng.choice([0, 3, 4, 5])
            to_call = rng.choice([0, 20, 60, 300])
            stack = rng.choice([30, 200, 1000])
            st = {'hole': cards[:2], 'board': cards[2:2 + nb], 'pot': 30 + to_call * 2,
                  'to_call': to_call, 'stack': stack, 'big_blind': 20,
                  'num_opponents': 1}
            out = b.act(st)
            assert_legal(self, (out['action'], out['amount']), Legal.from_state(st))
        self.assertEqual(b.stats['errors'], 0, b.stats['last_error'])
        self.assertEqual(b.stats['timeouts'], 0)


if __name__ == '__main__':
    unittest.main()

import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim', 'ours'))  # our frozen bot
import bot  # noqa: E402
from bot import CLASS_INDEX as CI, parse_cards  # noqa: E402

bot.install_tables(bot.load_tables())


def H(s):
    return parse_cards(s.split())


def eq1(name):
    return bot.PREFLOP_EQ[1][CI[name]]


def hu(a, b):
    return bot.HU_E[CI[a] * 169 + CI[b]]


def frac(table, depth_bb):
    bits = table[int(depth_bb * 2)]
    return sum(bot.CLASS_NCOMBOS[c] for c in range(169) if bits >> c & 1) / 1326.0


class TestTables(unittest.TestCase):
    def test_tables_present(self):
        self.assertEqual(sorted(bot.PREFLOP_EQ), [1])
        self.assertEqual(len(bot.PREFLOP_EQ[1]), 169)
        self.assertTrue(all(0.0 < v < 1.0 for v in bot.PREFLOP_EQ[1]))
        self.assertEqual(len(bot.HU_E), 169 * 169)
        self.assertEqual(sorted(bot.PUSH_TABLE), list(range(2, 41)))
        self.assertEqual(sorted(bot.CALL_TABLE), list(range(2, 41)))

    def test_known_hu_vs_random(self):
        # PokerStove reference values
        for name, ref in (('AA', 0.852), ('KK', 0.824), ('AKs', 0.670),
                          ('AKo', 0.654), ('22', 0.503), ('32o', 0.323)):
            self.assertAlmostEqual(eq1(name), ref, delta=0.004, msg=name)

    def test_hu_matrix_known(self):
        self.assertAlmostEqual(hu('AA', 'KK'), 0.819, delta=0.008)
        self.assertAlmostEqual(hu('AKo', 'QQ'), 0.431, delta=0.008)
        self.assertAlmostEqual(hu('22', 'AKo'), 0.525, delta=0.01)
        self.assertAlmostEqual(hu('AA', 'AA'), 0.5, delta=1e-4)
        for i, j in ((3, 77), (100, 168), (0, 168)):
            self.assertAlmostEqual(bot.HU_E[i * 169 + j] + bot.HU_E[j * 169 + i], 1.0, delta=1e-4)

    def test_weights_match_bruteforce(self):
        for a, b in (('AA', 'AA'), ('AKs', 'AKo'), ('AKo', 'AKo'), ('72o', 'KQs'), ('AA', 'AKs')):
            i, j = CI[a], CI[b]
            n = sum(1 for x in bot.CLASS_COMBOS[i] for y in bot.CLASS_COMBOS[j]
                    if not set(x) & set(y))
            self.assertEqual(bot.HU_W[i * 169 + j], n, (a, b))

    def test_matrix_consistent_with_table(self):
        worst = 0.0
        for i in range(169):
            num = sum(bot.HU_W[i * 169 + j] * bot.HU_E[i * 169 + j] for j in range(169))
            den = sum(bot.HU_W[i * 169 + j] for j in range(169))
            worst = max(worst, abs(num / den - bot.PREFLOP_EQ[1][i]))
        self.assertLess(worst, 0.005)

    def test_vs_range(self):
        aa = H('As Ad')
        self.assertAlmostEqual(bot.preflop_equity_vs_range(aa, [1.0] * 169), eq1('AA'), delta=0.005)
        kq = H('Ks Qd')
        self.assertLess(bot.preflop_equity_vs_range(kq, bot.top_range(0.05)),
                        bot.preflop_equity_vs_range(kq, bot.top_range(0.5)))
        w = bot.top_range(0.10)
        got = sum(w[i] * bot.CLASS_NCOMBOS[i] for i in range(169)) / 1326.0
        self.assertAlmostEqual(got, 0.10, delta=1e-9)

    def test_push_call_tables(self):
        aa, trash = H('As Ad'), H('7c 2d')
        for bb in (1, 3, 6.5, 10, 15, 20):
            self.assertTrue(bot.push_ok(aa, bb))
            self.assertTrue(bot.call_ok(aa, bb))
        self.assertFalse(bot.push_ok(trash, 10))
        self.assertFalse(bot.call_ok(trash, 10))
        self.assertTrue(bot.push_ok(H('Kc 2d'), 10))   # HU Nash 10bb: K2o shoves
        # Published HU Nash (no ante) at 10bb: SB shoves 57.8%, BB calls ~37%.
        self.assertAlmostEqual(frac(bot.PUSH_TABLE, 10), 0.578, delta=0.02)
        self.assertAlmostEqual(frac(bot.CALL_TABLE, 10), 0.37, delta=0.03)
        pushes = [frac(bot.PUSH_TABLE, bb) for bb in (2, 5, 10, 20)]
        self.assertEqual(pushes, sorted(pushes, reverse=True))


class TestMonteCarlo(unittest.TestCase):
    def test_matches_table(self):
        r = random.Random(5)
        for name in ('AA', 'T9s', '72o'):
            hero = list(bot.CLASS_COMBOS[CI[name]][0])
            e, k = bot.equity_mc(hero, [], 1, iters=40000, rng=r)
            self.assertAlmostEqual(e, eq1(name), delta=0.008, msg=name)

    def test_board_plays_tie(self):
        e, _ = bot.equity_mc(H('2c 3d'), H('Ts Js Qs Ks As'), 3, iters=2000)
        self.assertAlmostEqual(e, 0.25, delta=1e-9)

    def test_lock(self):
        e, _ = bot.equity_mc(H('Th 3d'), H('Ah Kh Qh Jh 2c'), 1, iters=2000)
        self.assertEqual(e, 1.0)

    def test_drawing_dead(self):
        e, _ = bot.equity_mc(H('2c 3d'), H('As Ad Ah Kc Kd'), 1,
                             ranges=[[tuple(H('Ac Ks'))]], iters=500)
        self.assertEqual(e, 0.0)

    def test_ranged_aa_vs_kk(self):
        aa = H('As Ah')
        kk = bot.range_combos([1.0 if i == CI['KK'] else 0 for i in range(169)])
        self.assertEqual(len(kk), 6)
        e, k = bot.equity_mc(aa, [], 1, ranges=[kk], iters=30000, rng=random.Random(9))
        self.assertAlmostEqual(e, 0.82, delta=0.012)

    def test_tight_range_lowers_equity(self):
        hero = H('Qs Qd')
        tight = bot.range_combos(bot.top_range(0.03))
        e_r, _ = bot.equity_mc(hero, [], 1, iters=20000, rng=random.Random(1))
        e_t, _ = bot.equity_mc(hero, [], 1, ranges=[tight], iters=20000, rng=random.Random(1))
        self.assertLess(e_t, e_r - 0.15)   # QQ: ~0.80 vs random, ~0.60 vs 88+

    def test_ranged_mc_matches_matrix(self):
        r = random.Random(3)
        for hole, frac_ in (('Qs Qd', 0.03), ('Ah 5h', 0.15), ('Kc Td', 0.40)):
            w = [1.0 if x >= 0.5 else 0.0 for x in bot.top_range(frac_)]  # as range_combos
            e_mc, _ = bot.equity_mc(H(hole), [], 1, ranges=[bot.range_combos(w)],
                                    iters=40000, rng=r)
            self.assertAlmostEqual(e_mc, bot.preflop_equity_vs_range(H(hole), w),
                                   delta=0.01, msg=hole)

    def test_deadline_caps_time(self):
        t0 = time.perf_counter()
        e, k = bot.equity_mc(H('As Kd'), H('2c 7h Td'), 1, iters=10 ** 7,
                             deadline=t0 + 0.02)
        self.assertLess(time.perf_counter() - t0, 0.05)
        self.assertGreater(k, 64)

    def test_deterministic(self):
        a = bot.equity_mc(H('As Kd'), H('2c 7h Td'), 1, iters=3000, rng=random.Random(42))
        b = bot.equity_mc(H('As Kd'), H('2c 7h Td'), 1, iters=3000, rng=random.Random(42))
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()

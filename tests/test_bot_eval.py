import os
import random
import sys
import unittest
from collections import Counter
from itertools import combinations

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim', 'ours'))  # our frozen bot
import bot  # noqa: E402
from bot import evaluate, parse_cards  # noqa: E402


def ref5(cards):
    """Slow, obviously-correct 5-card evaluator -> comparable tuple."""
    ranks = sorted((c >> 2 for c in cards), reverse=True)
    flush = len(set(c & 3 for c in cards)) == 1
    uniq = sorted(set(ranks), reverse=True)
    sh = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            sh = uniq[0]
        elif uniq == [12, 3, 2, 1, 0]:
            sh = 3
    groups = sorted(Counter(ranks).items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [g[1] for g in groups]
    order = [g[0] for g in groups]
    if sh is not None and flush:
        return (8, sh)
    if shape == [4, 1]:
        return (7,) + tuple(order)
    if shape == [3, 2]:
        return (6,) + tuple(order)
    if flush:
        return (5,) + tuple(ranks)
    if sh is not None:
        return (4, sh)
    if shape == [3, 1, 1]:
        return (3,) + tuple(order)
    if shape == [2, 2, 1]:
        return (2,) + tuple(order)
    if shape == [2, 1, 1, 1]:
        return (1,) + tuple(order)
    return (0,) + tuple(ranks)


def ref(cards):
    return max(ref5(c) for c in combinations(cards, 5))


def H(s):
    return parse_cards(s.split())


def sign(x):
    return (x > 0) - (x < 0)


class TestEvaluator(unittest.TestCase):
    def test_all_5card_hands(self):
        cats = Counter()
        values = set()
        for h in combinations(range(52), 5):
            v = evaluate(h)
            cats[v >> 20] += 1
            values.add(v)
        self.assertEqual(len(values), 7462)
        self.assertEqual([cats[k] for k in range(9)],
                         [1302540, 1098240, 123552, 54912, 10200, 5108, 3744, 624, 40])

    def test_random_7card_vs_reference(self):
        rng = random.Random(1)
        prev = None
        for _ in range(30000):
            h = rng.sample(range(52), 7)
            v, r = evaluate(h), ref(h)
            self.assertEqual(v >> 20, r[0], [bot.card_str(c) for c in h])
            if prev is not None:
                self.assertEqual(sign(v - prev[0]), sign((r > prev[1]) - (r < prev[1])))
            prev = (v, r)

    def test_random_6card_vs_reference(self):
        rng = random.Random(2)
        for _ in range(5000):
            h = rng.sample(range(52), 6)
            a = rng.sample(range(52), 6)
            self.assertEqual(sign(evaluate(h) - evaluate(a)),
                             (ref(h) > ref(a)) - (ref(h) < ref(a)))

    def test_known_orderings(self):
        chain = [
            '2c 3d 4h 5s 7c 8d 9h',      # 9-high
            '2c 2d 4h 5s 7c 8d 9h',      # pair
            '2c 2d 4h 4s 7c 8d 9h',      # two pair
            '2c 2d 4h 4s 7c 7d 9h',      # three pairs -> 77 44 9
            '2c 2d 2h 5s 7c 8d 9h',      # trips
            'Ac 2d 3h 4s 5c 9d Kh',      # wheel
            '2c 3d 4h 5s 6c 9d Kh',      # 6-high straight
            'Tc Jd Qh Ks Ac 2d 3h',      # broadway
            '2h 4h 6h 8h 9h Kc Kd',      # flush beats pair on board
            'Ah Kh Qh Jh 9h 2c 3d',      # A-high flush
            '2c 2d 2h 3s 3c 9d Kh',      # full house
            '2c 2d 2h 3s 3c 3d Kh',      # two trips -> 333 22
            '2c 2d 2h 2s 3c 9d Kh',      # quads
            'Ac 2c 3c 4c 5c 9d Kh',      # steel wheel
            'Tc Jc Qc Kc Ac 2d 3h',      # royal
        ]
        vals = [evaluate(H(s)) for s in chain]
        for a, b, s in zip(vals, vals[1:], chain[1:]):
            self.assertLess(a, b, s)

    def test_kickers(self):
        self.assertGreater(evaluate(H('Ac Ad Kh 9s 7c 4d 2h')), evaluate(H('Ac Ad Qh 9s 7c 4d 2h')))
        # board plays: both hands tie
        self.assertEqual(evaluate(H('Ac Kc Qd Jh Ts 2c 3d')), evaluate(H('Ac Kc Qd Jh Ts 4c 5d')))
        # quads: kicker from the remaining trips
        self.assertEqual(evaluate(H('9c 9d 9h 9s Kc Kd Kh')) >> 20, bot.QUADS)

    def test_5_6_7_card_keys_distinct(self):
        # same rank multiset sizes never collide (base-5 encoding)
        self.assertEqual(len(bot._NF), 6175 + 18395 + 49205)


if __name__ == '__main__':
    unittest.main()

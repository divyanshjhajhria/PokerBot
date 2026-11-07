import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'sim', 'ours'))  # our frozen bot
import bot  # noqa: E402

bot.install_tables(bot.load_tables())


def prof(**counts):
    p = bot.Profile()
    p.c.update(counts)
    return p


class TestProfile(unittest.TestCase):
    def test_empty_profile_is_the_prior(self):
        p = bot.Profile()
        for name, (_, _, prior) in bot.RATES.items():
            self.assertAlmostEqual(p.rate(name), prior)
        self.assertEqual(p.labels(), ['tag'])

    def test_beta_smoothing(self):
        p = prof(pre_opp=10, vpip=10)
        self.assertAlmostEqual(p.rate('vpip'), (10 + 0.35 * 10) / 20)

    def test_observe_adds_counts_and_first5_shoves(self):
        p = bot.Profile()
        for _ in range(7):
            p.observe({'pre_opp': 1, 'vpip': 1, 'pfr': 1, 'shoves': 1})
        self.assertEqual(p.c['hands'], 7)
        self.assertEqual(p.c['shoves'], 7)
        self.assertEqual(p.c['shoves_first5'], 5)
        p.observe({'hands': 99, 'bogus': 3})      # ignored keys
        self.assertEqual(p.c['hands'], 8)

    def test_maniac_rules(self):
        self.assertIn('maniac', prof(hands=5, pre_opp=5, shoves=3, shoves_first5=3).labels())
        self.assertIn('maniac', prof(hands=10, pre_opp=10, shoves=4).labels())   # 40% over 10
        self.assertNotIn('maniac', prof(hands=7, pre_opp=7, shoves=4).labels())  # too few hands
        self.assertNotIn('maniac', prof(hands=20, pre_opp=20, shoves=6).labels())  # 30%

    def test_station_nit_random_shove_over_raiser(self):
        self.assertEqual(prof(pre_opp=40, vpip=36, face_bet=30, fold_to_bet=1).label(), 'station')
        self.assertEqual(prof(pre_opp=60, vpip=3).label(), 'nit')
        self.assertEqual(prof(pre_opp=40, vpip=30, raises=12, overbets=8).label(), 'random')
        self.assertEqual(prof(face_raise=10, shove_over_raise=7).label(), 'shove_over_raiser')
        self.assertEqual(prof(pre_opp=40, vpip=12, pfr=9).label(), 'tag')

    def test_ranges(self):
        maniac = prof(hands=5, pre_opp=5, shoves=3, shoves_first5=3)
        self.assertEqual(maniac.range_frac('call'), 1.0)
        self.assertEqual(sum(maniac.range_weights('shove')), 169.0)
        nit = prof(pre_opp=200, vpip=10, pfr=6, first_in=100, open_shove=0)
        self.assertAlmostEqual(nit.range_frac('call'), (10 + 3.5) / 210)
        self.assertEqual(nit.range_frac('shove'), 0.02)     # floor
        self.assertLess(nit.range_frac('raise'), nit.range_frac('call'))
        w = nit.range_weights('call')
        self.assertEqual(w[bot.CLASS_INDEX['AA']], 1.0)
        self.assertEqual(w[bot.CLASS_INDEX['72o']], 0.0)

    def test_bot_keeps_profiles_per_name(self):
        b = bot.Bot()
        b.observe('alice', {'pre_opp': 1, 'vpip': 1})
        b.observe('bob', {'pre_opp': 1})
        self.assertEqual(b.profile('alice').c['vpip'], 1)
        self.assertEqual(b.profile('bob').c['vpip'], 0)
        b.new_match()
        self.assertEqual(b.profiles, {})


if __name__ == '__main__':
    unittest.main()

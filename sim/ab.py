"""Whole-bot paired A/B: the decider for promotions.

  .venv/bin/python sim/ab.py                                   # model.py vs zip v0
  .venv/bin/python sim/ab.py --cand sim/snapshots/zip_pA.py --base sim/guh/model.py
  .venv/bin/python sim/ab.py --deals 400 --seed 2 --opponents ExampleBot equity

Each bot is loaded from its file under its own module name, so two files
that are both called model.py can play in one process. Both bots play the
same decks from the same seats, two seat rotations per deck, a fresh bot per
rotation (as in the arena's duplicate mode), against an opponent rebuilt
with the same seed every hand, so the opponent's own randomness is paired
too. The verdict uses the per-deal difference, which cancels most card and
opponent luck.

Promotion rule: the realistic group (ExampleBot, TAGBot, equity,
JamOverRaiseBot, tight) is non-negative within its CI, and no opponent is
worse by more than its CI. Dev tool: never imported by model.py.
"""
import argparse
import importlib.util
import multiprocessing as mp
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SPARRING = ('ExampleBot', 'TAGBot', 'JamOverRaiseBot', 'LimpShoveBot', 'MinRaiseBot',
            'LLMGTOBot', 'ExampleV1', 'ExampleV2', 'ExampleV3', 'AntiAIBot')
REALISTIC = ('ExampleBot', 'TAGBot', 'equity', 'JamOverRaiseBot', 'tight')
DEFAULT_OPPONENTS = ('ExampleBot', 'TAGBot', 'equity', 'JamOverRaiseBot', 'tight',
                     'ExampleV1', 'ExampleV2', 'ExampleV3', 'LLMGTOBot', 'AntiAIBot',
                     'LimpShoveBot', 'MinRaiseBot', 'call', 'random', 'shove', 'fold')


def load_bot(spec, alias):
    """'path/to/file.py[:Class]' -> the class, imported as module `alias`
    with the file's own folder on sys.path for its imports."""
    path, _, cls = spec.partition(':')
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    for extra in (folder, os.path.join(ROOT, 'sim', 'ours')):
        if extra not in sys.path:
            sys.path.insert(1, extra)   # right after ROOT (engine modules first)
    spec_ = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec_)
    sys.modules[alias] = mod
    spec_.loader.exec_module(mod)
    return getattr(mod, cls or 'Model')


def _opponent(name, seed):
    """A dummy (arena/dummies.py) or a sparring bot (sim/bots/sparring.py)."""
    from arena.dummies import DUMMIES
    if name in DUMMIES:
        return DUMMIES[name](seed=seed)
    sys.path.insert(0, os.path.join(ROOT, 'sim', 'bots'))
    import sparring
    return getattr(sparring, name)()


class Timed(object):
    """Passes seed/run/end_game through, keeping the slowest run()."""

    def __init__(self, bot):
        self.bot = bot
        self.slowest = 0.0

    def seed(self, hand, player_id):
        self.bot.seed(hand, player_id)

    def run(self, actions, gs):
        t = time.perf_counter()
        try:
            return self.bot.run(actions, gs)
        finally:
            self.slowest = max(self.slowest, time.perf_counter() - t)

    def end_game(self, actions, gs):
        self.bot.end_game(actions, gs)


def run_one(args):
    opponent, deals, cand, base, seed = args
    from card_utils import full_deck
    from engine import play_hand
    classes = {'cand': load_bot(cand, 'ab_cand'), 'base': load_bot(base, 'ab_base')}
    nets = {'cand': [], 'base': []}
    faults = {'cand': 0, 'base': 0}
    slowest = {'cand': 0.0, 'base': 0.0}
    t0 = time.time()
    for who in ('cand', 'base'):
        for rot in range(2):
            m = Timed(classes[who]())
            for d in range(deals):
                deck = full_deck()
                random.Random('%d:%d' % (seed, d)).shuffle(deck)
                opp = _opponent(opponent, hash((seed, d, rot)) & 0xFFFFFFFF)
                seats, teams = (([m, opp], ['us', opponent]) if rot == 0
                                else ([opp, m], [opponent, 'us']))
                r = play_hand(seats, deck, [500, 500], button=0, teams=teams)
                nets[who].append(r.net[rot])
                faults[who] += rot in r.faults
            slowest[who] = max(slowest[who], m.slowest)
    return opponent, nets, faults, slowest, time.time() - t0


def mean_ci(xs, bb=20.0):
    n = len(xs)
    m = sum(xs) / float(n)
    sd = (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return 100 * m / bb, 100 * 1.96 * sd / n ** 0.5 / bb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cand', default='model.py:Model')
    ap.add_argument('--base', default='sim/guh/model.py:Model')
    ap.add_argument('--deals', type=int, default=400)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--opponents', nargs='+', default=list(DEFAULT_OPPONENTS))
    args = ap.parse_args()
    jobs = [(o, args.deals, args.cand, args.base, args.seed) for o in args.opponents]
    t0 = time.time()
    with mp.Pool(min(len(jobs), os.cpu_count() or 2)) as pool:
        results = pool.map(run_one, jobs)
    print('cand %s  vs  base %s  |  %d deals x 2 seats, seed %d, %.0fs'
          % (args.cand, args.base, args.deals, args.seed, time.time() - t0))
    print('%-16s %17s %17s %19s %8s %7s %16s' % (
        'opponent', 'cand bb/100', 'base bb/100', 'paired diff', 'verdict', 'faults',
        'slowest cand/base'))
    ok, real_sum, real_var = True, 0.0, 0.0
    worst_move = 0.0
    for opp, nets, faults, slowest, secs in results:
        c, cci = mean_ci(nets['cand'])
        b, bci = mean_ci(nets['base'])
        d, dci = mean_ci([x - y for x, y in zip(nets['cand'], nets['base'])])
        verdict = 'better' if d - dci > 0 else 'worse' if d + dci < 0 else 'unclear'
        if verdict == 'worse':
            ok = False
        if opp in REALISTIC:
            real_sum += d
            real_var += (dci / 1.96) ** 2
        worst_move = max(worst_move, slowest['cand'])
        print('%-16s %+8.1f ± %-6.1f %+8.1f ± %-6.1f %+9.1f ± %-6.1f %8s %7d %7.3f/%.3fs'
              % (opp, c, cci, b, bci, d, dci, verdict, faults['cand'], slowest['cand'],
                 slowest['base']))
    real_ci = 1.96 * real_var ** 0.5
    real_ok = real_sum + real_ci >= 0
    print('realistic group (sum of diffs): %+.1f ± %.1f  -> %s'
          % (real_sum, real_ci, 'non-negative' if real_ok else 'NEGATIVE'))
    print('slowest candidate move: %.3fs (%s 0.5 s)' % (worst_move, '<' if worst_move < 0.5 else '>='))
    print('PROMOTE' if ok and real_ok and worst_move < 0.5 else 'DO NOT PROMOTE')


if __name__ == '__main__':
    main()

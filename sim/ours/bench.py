"""Benchmarks for the evaluator, equity code, pickle and decision latency.

  python3 bench.py
"""
import os
import pickle
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot  # noqa: E402

pc = time.perf_counter


def rate(fn, n):
    t = pc()
    fn()
    return n / (pc() - t)


def main():
    print('Python %s' % sys.version.split()[0])
    # 1. Import + pickle costs in a fresh interpreter.
    ts = []
    for _ in range(5):
        out = subprocess.check_output(
            [sys.executable, '-c',
             'import time,sys; sys.path.insert(0, %r); t=time.perf_counter(); import bot; '
             'print(time.perf_counter()-t)' % HERE])
        ts.append(float(out))
    print('import bot (evaluator tables built): median %.0f ms' % (1000 * sorted(ts)[2]))
    t = pc()
    b = bot.Bot()
    print('Bot() incl. loading tables_data:      %.0f ms' % (1000 * (pc() - t)))
    blob = pickle.dumps(b)
    t = pc()
    pickle.loads(blob)
    print('pickle: %d KB, unpickle %.0f ms' % (len(blob) // 1024, 1000 * (pc() - t)))

    # 2. Evaluator.
    r = random.Random(1)
    ev = bot.evaluate
    for k in (5, 6, 7):
        hands = [r.sample(range(52), k) for _ in range(200000)]

        def run():
            for h in hands:
                ev(h)
        print('evaluate() %d-card: %.2f M/s' % (k, rate(run, len(hands)) / 1e6))

    # 3. O(1) preflop lookups.
    hole = bot.parse_cards(['Ah', 'Td'])
    w = bot.top_range(0.15)
    N = 20000
    for name, fn in (('preflop_equity', lambda: bot.preflop_equity(hole, 1)),
                     ('preflop_equity_vs_range', lambda: bot.preflop_equity_vs_range(hole, w)),
                     ('push_ok', lambda: bot.push_ok(hole, 7.5))):
        def run():
            for _ in range(N):
                fn()
        print('%-24s %7.2f us/call' % (name, 1e6 / rate(run, N)))

    # 4. Monte Carlo throughput, heads-up.
    budget = bot.CONFIG['mc_iters'][1]
    print('\nMonte Carlo heads-up, budget %d iterations' % budget)
    print('  street    random it/s   top-15% it/s   ms/decision (random / ranged)')
    boards = {'preflop': [], 'flop': ['2c', '7h', 'Td'], 'turn': ['2c', '7h', 'Td', 'Ks'],
              'river': ['2c', '7h', 'Td', 'Ks', '3h']}
    combos = bot.range_combos(w)
    for street, bd in boards.items():
        brd = bot.parse_cards(bd)
        iters = 40000
        t = pc()
        bot.equity_mc(hole, brd, 1, iters=iters, rng=random.Random(3))
        rr = iters / (pc() - t)
        t = pc()
        bot.equity_mc(hole, brd, 1, ranges=[combos], iters=iters, rng=random.Random(3))
        rg = iters / (pc() - t)
        print('  %-7s  %11.0f   %12.0f   %6.1f / %.1f'
              % (street, rr, rg, 1000 * budget / rr, 1000 * budget / rg))

    brd = bot.parse_cards(boards['flop'])
    es = [bot.equity_mc(hole, brd, 1, iters=budget, rng=random.Random(s))[0] for s in range(30)]
    m = sum(es) / len(es)
    sd = (sum((e - m) ** 2 for e in es) / (len(es) - 1)) ** 0.5
    print('MC flop equity %.3f, SD across seeds %.4f' % (m, sd))

    # 5. Full decision latency through the safety shell.
    b = bot.Bot(seed=1)
    r = random.Random(5)
    for label, nb in (('preflop', 0), ('flop', 3), ('turn', 4), ('river', 5)):
        lat = []
        for _ in range(200):
            cards = r.sample(range(52), 2 + nb)
            st = {'hole': cards[:2], 'board': cards[2:], 'pot': 300, 'to_call': 100,
                  'stack': 2000, 'big_blind': 20, 'num_opponents': 1}
            t = pc()
            b.act(st)
            lat.append(pc() - t)
        lat.sort()
        print('act() %-7s: median %6.2f ms  p99 %6.2f ms  max %6.2f ms'
              % (label, 1e3 * lat[100], 1e3 * lat[197], 1e3 * lat[-1]))
    lim = bot.CONFIG['time_limit']
    print('worst decision %.1f ms = %.2f%% of %.0fs limit; errors=%d timeouts=%d'
          % (1e3 * b.stats['max_time'], 100 * b.stats['max_time'] / lim, lim,
             b.stats['errors'], b.stats['timeouts']))


if __name__ == '__main__':
    main()

"""Fault audit (Step 6): N hands per opponent, stressing short stacks.

  .venv/bin/python sim/fault_audit.py --hands 2000 [--bot model.py:Model]

Plays the bot heads-up through engine.play_hand (exactly the engine's rules
and move validation) against every opponent, alternating seats. A third of
the hands are full 500/500; the rest stress the edges: a stack below the big
blind (the blind goes in all-in for less), stacks of 1-2 big blinds (all-ins
that are short of a full raise, so betting is not reopened), lopsided stacks
(one side covered), and both sides tiny. Counts engine faults (exceptions,
illegal or invalid moves) and exceptions the bot swallows internally (the zip
bot's run() turns any exception into check/fold, which would hide bugs).
Dev tool: never imported by model.py.
"""
import argparse
import multiprocessing as mp
import os
import random
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(1, os.path.join(ROOT, 'sim'))
os.chdir(ROOT)

OPPONENTS = ('fold', 'call', 'random', 'shove', 'tight', 'equity', 'ExampleBot',
             'TAGBot', 'JamOverRaiseBot', 'LimpShoveBot', 'MinRaiseBot', 'LLMGTOBot',
             'ExampleV1', 'ExampleV2', 'ExampleV3', 'AntiAIBot', 'ours', 'zip_v0')


def stacks_for(rng):
    kind = rng.random()
    if kind < 1 / 3.0:
        return [500, 500], 'full'
    if kind < 0.5:
        return [rng.randint(1, 19), rng.randint(20, 1000)], 'below_bb'
    if kind < 2 / 3.0:
        return [rng.randint(20, 40), rng.randint(20, 1000)], 'short_allin'
    if kind < 5 / 6.0:
        return [rng.randint(41, 200), rng.randint(600, 2000)], 'covered'
    return [rng.randint(1, 45), rng.randint(1, 45)], 'both_tiny'


def audit(args):
    opponent, bot_spec, hands, seed = args
    import ab
    from card_utils import full_deck
    from engine import play_hand
    cls = ab.load_bot(bot_spec, 'audit_bot')
    swallowed = []
    for name in ('_decide', 'end_game'):
        orig = getattr(cls, name, None)
        if orig is None:
            continue

        def wrap(self, *a, _o=orig, **k):
            try:
                return _o(self, *a, **k)
            except Exception:
                swallowed.append(traceback.format_exc(limit=4))
                raise
        setattr(cls, name, wrap)
    if opponent == 'ours':
        opp_cls = ab.load_bot('sim/ours/ours_model.py:Model', 'audit_ours')
    elif opponent == 'zip_v0':
        opp_cls = ab.load_bot('sim/guh/model.py:Model', 'audit_v0')
    else:
        opp_cls = None
    rng = random.Random(seed)
    bot = ab.Timed(cls())
    faults, kinds, first_fault = 0, {}, None
    t0 = time.time()
    for h in range(hands):
        deck = full_deck()
        rng.shuffle(deck)
        stacks, kind = stacks_for(rng)
        if rng.random() < 0.5:
            stacks = stacks[::-1]
        opp = opp_cls() if opp_cls else ab._opponent(opponent, rng.randrange(1 << 30))
        us = h % 2
        seats = [bot, opp] if us == 0 else [opp, bot]
        teams = ['us', opponent] if us == 0 else [opponent, 'us']
        r = play_hand(seats, deck, stacks, button=rng.randrange(2), teams=teams)
        kinds[kind] = kinds.get(kind, 0) + 1
        if us in r.faults:
            faults += 1
            first_fault = first_fault or '%s (%s stacks %s)' % (r.faults[us], kind, stacks)
    return (opponent, hands, faults, len(swallowed), first_fault,
            swallowed[0] if swallowed else None, bot.slowest, kinds, time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bot', default='model.py:Model')
    ap.add_argument('--hands', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--opponents', nargs='+', default=list(OPPONENTS))
    args = ap.parse_args()
    jobs = [(o, args.bot, args.hands, args.seed + i) for i, o in enumerate(args.opponents)]
    t0 = time.time()
    with mp.Pool(min(len(jobs), os.cpu_count() or 2)) as pool:
        out = pool.map(audit, jobs, chunksize=1)
    print('fault audit: %s, %d hands per opponent, %.0f s' % (args.bot, args.hands, time.time() - t0))
    print('%-16s %6s %7s %10s %9s' % ('opponent', 'hands', 'faults', 'swallowed', 'slowest'))
    total_f = total_s = 0
    for opp, n, f, sw, ff, swtb, slow, kinds, secs in out:
        total_f += f
        total_s += sw
        print('%-16s %6d %7d %10d %8.3fs' % (opp, n, f, sw, slow))
        if ff:
            print('    first fault: %s' % ff)
        if swtb:
            print('    first swallowed exception:\n' + swtb)
    print('stack mix per opponent: %s' % out[0][7])
    print('TOTAL faults %d, swallowed exceptions %d -> %s'
          % (total_f, total_s, 'CLEAN' if total_f == 0 and total_s == 0 else 'NOT CLEAN'))


if __name__ == '__main__':
    main()

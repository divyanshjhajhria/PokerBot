"""Offline table generation -> tables_data.py (generated; do not edit).

Bot.__init__ loads tables_data.py and keeps the tables on the instance, so the
submitted pickle is self-contained.

  python3 precompute.py                 # heads-up defaults
  python3 precompute.py --quick         # small sample counts (smoke test)

Tables
  PREFLOP_EQ[n][cls]  equity of each of 169 classes vs n random hands
  HU matrix           equity + combo-pair weight of class i vs class j
  PUSH_HEX[2*bb]      HU Nash SB first-in shove set, 1-20 bb
  CALL_HEX[2*bb]      HU Nash BB call set vs that shove
"""
import argparse
import multiprocessing as mp
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bot  # noqa: E402

OUT_PATH = os.path.join(HERE, 'tables_data.py')
MAX_DEPTH_HALF_BB = 40   # push/call tables cover 1..20 bb in half-bb steps


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------
def _job_vs_random(args):
    cls, n, samples, seed = args
    hero = list(bot.CLASS_COMBOS[cls][0])  # suit-isomorphic: any combo works
    eq, k = bot.equity_mc(hero, [], n, iters=samples, rng=random.Random(seed))
    return cls, n, eq


def _job_hu_row(args):
    i, samples, seed = args
    rnd = random.Random(seed).random
    CV, NF, flush = bot._CV, bot._NF, bot._flush_value
    out = []
    for j in range(i, 169):
        pairs = [(a, b) for a in bot.CLASS_COMBOS[i] for b in bot.CLASS_COMBOS[j]
                 if a[0] not in b and a[1] not in b]
        w = len(pairs)
        if i == j or w == 0:
            out.append((j, 0.5, w))
            continue
        used = [False] * 52
        deck = list(range(52))
        total = 0.0
        for s in range(samples):
            (h1, h2), (o1, o2) = pairs[s % w]
            used[h1] = used[h2] = used[o1] = used[o2] = True
            brd = []
            k = 0
            while len(brd) < 5:
                r = k + int(rnd() * (52 - k))
                deck[k], deck[r] = deck[r], deck[k]
                c = deck[k]
                if not used[c]:
                    brd.append(c)
                k += 1
            used[h1] = used[h2] = used[o1] = used[o2] = False
            bt = CV[brd[0]] + CV[brd[1]] + CV[brd[2]] + CV[brd[3]] + CV[brd[4]]
            t = bt + CV[h1] + CV[h2]
            hv = flush(brd + [h1, h2], t) if (t + 0x3333) & 0x8888 else NF[t >> 16]
            t = bt + CV[o1] + CV[o2]
            ov = flush(brd + [o1, o2], t) if (t + 0x3333) & 0x8888 else NF[t >> 16]
            if hv > ov:
                total += 1.0
            elif hv == ov:
                total += 0.5
        out.append((j, total / samples, w))
    return i, out


# ---------------------------------------------------------------------------
# Push/fold: fictitious play in a simplified n-caller model (numpy, offline)
# ---------------------------------------------------------------------------
def solve_push_fold(E, W, n_behind, S, ante=0.0, iters=600):
    """Return per-class push probability for a first-in shove of S bb.

    Model: everyone has S bb; players behind call independently with their
    best response vs the pusher's average range (overcalls ignored); the
    pusher best-responds to the callers' average ranges, with card removal
    from W. Blinds 0.5/1, optional ante per player (all dead).
    """
    import numpy as np
    n_combos = np.array(bot.CLASS_NCOMBOS, dtype=float)
    condP = W / (n_combos[:, None] * 1225.0)          # P(other hand j | ours i)
    A = condP * E
    T = n_behind + 1
    if n_behind == 1:
        p0, posted = 0.5, [1.0]                          # SB vs BB
    else:
        p0, posted = 0.0, [0.0] * (n_behind - 2) + [0.5, 1.0]
    a = ante
    g_fold = 1.5 + T * a - p0 - a                       # steal gain
    ev_fold = -(p0 + a)
    pots = [2 * (S - a) + T * a + (1.5 - p0 - pk) for pk in posted]
    costs = [S - pk - a for pk in posted]               # caller's extra chips

    push = np.ones(169)
    calls = [np.full(169, 0.3) for _ in posted]
    for t in range(1, iters + 1):
        # Callers' best responses to the pusher's average range.
        pw = condP * push[None, :]                       # [j, i]
        den = pw.sum(axis=1)
        eq_call = np.where(den > 0, (pw * E).sum(axis=1) / np.maximum(den, 1e-12), 0.0)
        br_calls = [(eq_call * pot > cost).astype(float) for pot, cost in zip(pots, costs)]
        # Pusher's best response to the callers' average ranges.
        reach = np.ones(169)
        ev = np.zeros(169)
        for c, pot in zip(calls, pots):
            pc = condP @ c
            win = A @ c
            ev += reach * (win * pot - pc * S)
            reach = reach * (1.0 - pc)
        ev += reach * g_fold
        br_push = (ev > ev_fold).astype(float)
        lr = 1.0 / (t + 1)
        push += (br_push - push) * lr
        for k in range(len(calls)):
            calls[k] += (br_calls[k] - calls[k]) * lr
    return push, calls


# ---------------------------------------------------------------------------
# Baking
# ---------------------------------------------------------------------------
def _hex_block(name, hexstr, width=120):
    lines = ['%s = (' % name]
    for k in range(0, len(hexstr), width):
        lines.append("    '%s'" % hexstr[k:k + width])
    lines.append(')')
    return '\n'.join(lines)


def _bits_block(name, table):
    rows = ['%s = {' % name]
    for d in sorted(table):
        rows.append("    %d: '%s'," % (d, table[d]))
    rows.append('}')
    return '\n'.join(rows)


def write_tables(meta, pre, E, W, push_hex, call_hex):
    import numpy as np
    eq16 = np.clip(np.round(E * 65535), 0, 65535).astype('>u2').tobytes().hex()
    w8 = np.asarray(W, dtype=np.uint8).tobytes().hex()
    parts = ['"""Generated by precompute.py -- do not edit. See precompute.py for',
             'how each table is built; bot.load_tables() decodes them."""',
             '',
             'META = %r' % (meta,)]
    rows = ['PREFLOP_EQ = {']
    for n in sorted(pre):
        vals = ', '.join('%.5f' % v for v in pre[n])
        rows.append('    %d: [%s],' % (n, vals))
    rows.append('}')
    parts.append('\n'.join(rows))
    parts.append('# 169x169 uint16 big-endian, equity * 65535, row = our class')
    parts.append(_hex_block('HU_EQ_HEX', eq16))
    parts.append('# 169x169 uint8, non-conflicting combo pairs')
    parts.append(_hex_block('HU_W_HEX', w8))
    parts.append('# {2*bb: 169-bit mask over class index}')
    parts.append(_bits_block('PUSH_HEX', push_hex))
    parts.append(_bits_block('CALL_HEX', call_hex))
    src = '\n'.join(parts) + '\n'
    with open(OUT_PATH, 'w') as f:
        f.write(src)
    return len(src)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--samples', type=int, default=500000, help='per class per n (vs random)')
    ap.add_argument('--hu-samples', type=int, default=20000, help='per class pair')
    ap.add_argument('--max-opp', type=int, default=bot.CONFIG['max_players'] - 1)
    ap.add_argument('--ante', type=float, default=0.0, help='ante in bb (push/fold)')
    ap.add_argument('--workers', type=int, default=os.cpu_count())
    ap.add_argument('--quick', action='store_true')
    args = ap.parse_args()
    if args.quick:
        args.samples, args.hu_samples = 5000, 300
    import numpy as np

    t_all = time.time()
    pool = mp.Pool(args.workers)

    # 1. Equity vs n random opponents.
    t0 = time.time()
    jobs = [(c, n, args.samples, 1000003 * n + c) for n in range(1, args.max_opp + 1)
            for c in range(169)]
    jobs.sort(key=lambda j: -j[1])
    pre = dict((n, [0.0] * 169) for n in range(1, args.max_opp + 1))
    for c, n, eq in pool.imap_unordered(_job_vs_random, jobs, chunksize=4):
        pre[n][c] = eq
    t_pre = time.time() - t0
    print('vs-random table: %d classes x %d opp x %d samples in %.1fs'
          % (169, args.max_opp, args.samples, t_pre))

    # 2. Heads-up class-vs-class matrix.
    t0 = time.time()
    E = np.full((169, 169), 0.5)
    W = np.zeros((169, 169))
    rows = [(i, args.hu_samples, 7919 * i + 17) for i in range(169)]
    for i, out in pool.imap_unordered(_job_hu_row, rows, chunksize=1):
        for j, eq, w in out:
            E[i, j], E[j, i] = eq, 1.0 - eq
            W[i, j] = W[j, i] = w
    pool.close()
    t_hu = time.time() - t0
    print('HU matrix: 14365 pairs x %d samples in %.1fs' % (args.hu_samples, t_hu))

    # Cross-check: matrix row vs random == vs-random table (n=1).
    rowavg = (W * E).sum(axis=1) / W.sum(axis=1)
    diff = np.abs(rowavg - np.array(pre[1]))
    print('cross-check matrix vs table (n=1): max |diff| %.4f, mean %.4f'
          % (diff.max(), diff.mean()))

    # 3. Heads-up push/fold (SB shoves first-in, BB calls or folds).
    t0 = time.time()
    push_hex, call_hex = {}, {}
    n_combos = np.array(bot.CLASS_NCOMBOS, dtype=float)

    def mask(v):
        bits = 0
        for c in range(169):
            if v[c] >= 0.5:
                bits |= 1 << c
        return '%x' % bits

    print('  stack bb   SB push%   BB call%')
    for d in range(2, MAX_DEPTH_HALF_BB + 1):
        S = d / 2.0
        push, calls = solve_push_fold(E, W, 1, S, ante=args.ante)
        push_hex[d], call_hex[d] = mask(push), mask(calls[-1])
        if S in (2.0, 5.0, 8.0, 10.0, 15.0, 20.0):
            print('  %8.1f   %7.1f   %8.1f' % (
                S, 100 * (n_combos * (push >= 0.5)).sum() / 1326.0,
                100 * (n_combos * (calls[-1] >= 0.5)).sum() / 1326.0))
    print('push/fold: %d depths in %.1fs' % (MAX_DEPTH_HALF_BB - 1, time.time() - t0))

    meta = {'samples_vs_random': args.samples, 'hu_samples': args.hu_samples,
            'max_opp': args.max_opp, 'ante_bb': args.ante,
            'seconds': round(time.time() - t_all, 1)}
    size = write_tables(meta, pre, E, W, push_hex, call_hex)
    print('wrote tables_data.py (%d KB), total %.1fs' % (size // 1024, time.time() - t_all))


if __name__ == '__main__':
    main()

"""Full round robin, scored like the event (Step 5).

  .venv/bin/python sim/roundrobin.py --deals 200 PLAYER ...

Every pair of players plays one duplicate match through arena.match.play_match
(team pickles in their own worker processes, dummies in-process), pairings
run in parallel, and standings come from the organisers' own
evals.score.leaderboard: bb/100 over every hand played, with a 95% CI.
PLAYER is a .pkl path or a dummy name. Dev tool: never imported by model.py.
"""
import argparse
import itertools
import multiprocessing as mp
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def play_pair(args):
    a, b, deals, seed = args
    from arena.match import MatchConfig, play_match
    from arena.players import player
    t = time.time()
    log = play_match([player(a), player(b)],
                     MatchConfig(deals=deals, seed=seed, checkpoint_dir=None))
    return log, time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('players', nargs='+')
    ap.add_argument('--deals', type=int, default=200)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--procs', type=int, default=os.cpu_count() or 2)
    ap.add_argument('--focus', default='byteclub', help='player whose pairings to list')
    args = ap.parse_args()
    from evals.score import leaderboard
    pairs = [(a, b, args.deals, args.seed) for a, b in itertools.combinations(args.players, 2)]
    t0 = time.time()
    with mp.Pool(args.procs) as pool:
        results = pool.map(play_pair, pairs, chunksize=1)
    logs = [log for log, _ in results]
    faults, slowest = {}, {}
    for log in logs:
        for hand in log.hands:
            for seat, name in enumerate(hand.seats):
                faults[name] = faults.get(name, 0) + (seat in hand.faults)
        for name, sec in log.max_move_seconds.items():
            slowest[name] = max(slowest.get(name, 0.0), sec)
    print('%d players, %d pairings, %d deals x 2 seats each, %.0f s'
          % (len(args.players), len(pairs), args.deals, time.time() - t0))
    print('%3s %-18s %20s %8s %7s %8s' % ('#', 'player', 'bb/100', 'hands', 'faults', 'slowest'))
    for rank, s in enumerate(leaderboard(logs), 1):
        print('%3d %-18s %+9.1f ± %-8.1f %8d %7d %7.2fs'
              % (rank, s.name, s.bb_per_100, s.ci95, s.hands, faults.get(s.name, 0),
                 slowest.get(s.name, 0.0)))
    focus = args.focus
    rows = []
    for log in logs:
        if focus not in log.names:
            continue
        other = [n for n in log.names if n != focus][0]
        won = sum(h.net[h.seats.index(focus)] for h in log.hands)
        rows.append((won / float(len(log.hands)) / 20.0 * 100.0, other))
    if rows:
        print('\n%s by opponent (bb/100):' % focus)
        for v, other in sorted(rows):
            print('   %-18s %+8.1f' % (other, v))


if __name__ == '__main__':
    main()

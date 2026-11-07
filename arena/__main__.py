"""
Runs matches between team pickles and dummy bots, and stores the results.

    python -m arena bots/                      # every bots/*/*.pkl, a table
    python -m arena bots/ call --format round-robin --deals 1000
    python -m arena bots/ --mode tournament --bankroll bankroll.json

PLAYER is a .pkl file (its stem is the team name), a folder (every .pkl in
it and in its subfolders one level down: one folder per team) or a dummy
name. Every match is appended to the results file; score it with
python -m evals --leaderboard RESULTS. Tournament mode carries stacks in
the bankroll file, drops teams with no chips, and checkpoints every team's
model to --checkpoints after each match.
"""

import argparse
import dataclasses
import glob
import itertools
import os
import shlex

from arena.match import MatchConfig, MatchLog, play_match
from arena.players import Player, player
from arena.remote import TIME_LIMIT
from arena.results import ResultStore, load_bankroll, save_bankroll


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="python -m arena")
    parser.add_argument("players", nargs="+", metavar="PLAYER")
    parser.add_argument("--mode", choices=["duplicate", "tournament"],
                        default="duplicate")
    parser.add_argument("--format", choices=["table", "round-robin"],
                        default="table",
                        help="one table with everyone, or every pair")
    parser.add_argument("--deals", type=int, default=500)
    parser.add_argument("--times", type=int, default=1,
                        help="repeat every match with new decks")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--stack", type=int, default=500)
    parser.add_argument("--blinds", type=int, nargs=2, default=[10, 20],
                        metavar=("SMALL", "BIG"))
    parser.add_argument("--results", default="results/results.jsonl")
    parser.add_argument("--bankroll", help="tournament: team chips file")
    parser.add_argument("--checkpoints", default="checkpoints")
    parser.add_argument("--no-checkpoints", action="store_true")
    parser.add_argument("--timeout", type=float, default=TIME_LIMIT)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--launcher", default="",
                        help='sandbox prefix, e.g. "bwrap --ro-bind / /"')
    args = parser.parse_args(argv)

    players = [p for spec in args.players for p in _expand(spec)]
    groups = (
        [players] if args.format == "table"
        else [list(pair) for pair in itertools.combinations(players, 2)]
    )
    store = ResultStore(args.results)
    bankroll = load_bankroll(args.bankroll) if args.bankroll else {}
    for i in range(args.times):
        for group in groups:
            config = MatchConfig(
                deals=args.deals, mode=args.mode, seed=args.seed + i,
                stack=args.stack, small_blind=args.blinds[0],
                big_blind=args.blinds[1], timeout=args.timeout,
                launcher=tuple(shlex.split(args.launcher)),
                memory_mb=args.memory_mb,
                checkpoint_dir=(
                    None if args.no_checkpoints else args.checkpoints
                ),
            )
            if args.mode == "tournament":
                chips = {p.name: bankroll.get(p.name, args.stack)
                         for p in group}
                out = [p.name for p in group if chips[p.name] <= 0]
                group = [p for p in group if chips[p.name] > 0]
                if out:
                    print(f"out of chips: {', '.join(out)}")
                if len(group) < 2:
                    print("fewer than 2 teams with chips: skipped")
                    continue
                config = dataclasses.replace(
                    config, stacks={p.name: chips[p.name] for p in group}
                )
            log = play_match(group, config)
            store.add(log)
            if log.final_stacks is not None and args.bankroll:
                bankroll.update(log.final_stacks)
                save_bankroll(args.bankroll, bankroll)
            _report(log)
    print(f"results in {args.results}; rank them with "
          f"python -m evals --leaderboard {args.results}")


def _expand(spec: str) -> list[Player]:
    """A folder becomes its pickles (one level of team folders too)."""
    if os.path.isdir(spec):
        paths = sorted(glob.glob(os.path.join(spec, "*.pkl"))
                       + glob.glob(os.path.join(spec, "*", "*.pkl")))
        if not paths:
            raise SystemExit(f"no .pkl files in {spec}")
        return [player(path) for path in paths]
    return [player(spec)]


def _report(log: MatchLog):
    """Chips won per player, raw: scoring belongs to evals."""
    won = {name: 0 for name in log.names}
    faults = {name: 0 for name in log.names}
    for hand in log.hands:
        for seat, name in enumerate(hand.seats):
            won[name] += hand.net[seat]
            faults[name] += seat in hand.faults
    print(f"{log.config.mode} match, {len(log.hands)} hands, "
          f"seed {log.config.seed}:")
    for name in log.names:
        stack = ("" if log.final_stacks is None
                 else f"  stack {log.final_stacks[name]}")
        print(f"  {name:<24} {won[name]:>+9} chips  "
              f"{faults[name]} faults{stack}")


if __name__ == "__main__":
    main()

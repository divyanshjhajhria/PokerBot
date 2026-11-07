"""
Measures your model against dummy bots and other teams' pickles.

    python -m evals                      # heads-up against every dummy
    python -m evals --opponents tight bots/team_b/team_b.pkl --deals 1000
    python -m evals --table              # also one table with everyone
    python -m evals --leaderboard results/results.jsonl

Run from the repo root. Your model is pickled once and played from that
pickle in its own worker process, exactly as a submission is. bb/100 is big
blinds won per 100 hands, with its 95% confidence interval: the verdict is
"ahead" or "behind" only when the interval does not include 0. Beating the
dummies does not predict a tournament result: that depends on the other
teams' bots and on the scoring.
"""

import argparse
import importlib
import os
import pickle
import tempfile

from arena.dummies import DUMMIES
from arena.match import MatchConfig, repeat_match
from arena.players import Player, player
from arena.remote import TIME_LIMIT
from arena.results import ResultStore
from evals.score import MatchResult, leaderboard, score


def measure(play, players: list[Player], config: MatchConfig,
            times: int) -> MatchResult:
    """Plays times matches with play (arena's repeat_match, or a stand-in
    with the same signature) and scores them together."""
    return score(play(players, config, times))


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(prog="python -m evals")
    parser.add_argument(
        "--model", default="model:Model",
        help="your model as module or module:Class (default model:Model)",
    )
    parser.add_argument("--name", default="you", help="your team name")
    parser.add_argument(
        "--opponents", nargs="+", default=list(DUMMIES),
        help="dummy names and/or .pkl files (default: every dummy)",
    )
    parser.add_argument(
        "--table", action="store_true",
        help="also play one table with you and every opponent",
    )
    parser.add_argument("--deals", type=int, default=500)
    parser.add_argument("--times", type=int, default=1,
                        help="repeat each match with new decks")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=TIME_LIMIT)
    parser.add_argument(
        "--leaderboard", metavar="RESULTS",
        help="instead: rank everyone in an arena results file",
    )
    args = parser.parse_args(argv)

    if args.leaderboard:
        _print_leaderboard(args.leaderboard)
        return
    opponents = [player(spec) for spec in args.opponents]
    if args.name in {p.name for p in opponents}:
        parser.error(f"--name {args.name!r} is also an opponent's name")
    config = MatchConfig(deals=args.deals, seed=args.seed,
                         timeout=args.timeout)
    with tempfile.TemporaryDirectory() as folder:
        you = _pickle_model(args.model, args.name, folder)
        print(f"{'opponents':<24} {'your bb/100':>18} {'verdict':>8} "
              f"{'hands':>7} {'faults':>6} {'slowest':>8}")
        for opponent in opponents:
            result = measure(repeat_match, [you, opponent], config,
                             args.times)
            _report(opponent.name, result)
        if args.table and len(opponents) > 1:
            result = measure(repeat_match, [you] + opponents, config,
                             args.times)
            _report(f"table of {len(result.names)}", result)


def _pickle_model(spec: str, name: str, folder: str) -> Player:
    """Pickles your model to folder/name.pkl, checking it unpickles."""
    module, _, attribute = spec.partition(":")
    model_class = getattr(importlib.import_module(module),
                          attribute or "Model")
    blob = pickle.dumps(model_class())
    pickle.loads(blob)  # fail here, not in every worker
    path = os.path.join(folder, name + ".pkl")
    with open(path, "wb") as file:
        file.write(blob)
    return player(path)


def _report(label: str, result: MatchResult):
    """One row for the first player (yours), then its first fault."""
    bb, ci = result.bb_per_100[0], result.ci95[0]
    verdict = (
        "ahead" if bb - ci > 0 else "behind" if bb + ci < 0 else "unclear"
    )
    print(f"{label:<24} {bb:>+9.1f} ± {ci:<6.1f} {verdict:>8} "
          f"{result.hands:>7} {result.faults[0]:>6} "
          f"{result.max_move_seconds[0]:>7.2f}s")
    if result.first_faults[0] is not None:
        print(f"    first fault: {result.first_faults[0]}")


def _print_leaderboard(path: str):
    standings = leaderboard(ResultStore(path).logs())
    print(f"{'#':>3} {'team':<24} {'bb/100':>18} {'hands':>8}")
    for rank, s in enumerate(standings, 1):
        print(f"{rank:>3} {s.name:<24} {s.bb_per_100:>+9.1f} ± "
              f"{s.ci95:<6.1f} {s.hands:>8}")


if __name__ == "__main__":
    main()

from dataclasses import dataclass

from arena.match import MatchLog
from evals.stats import ci95, mean_and_se, pool


@dataclass
class MatchResult:
    """Duplicate matches between the same players, scored together."""

    names: list[str]
    hands: int
    matches: int
    bb_per_100: list[float]  # per name, over every match
    ci95: list[float]
    match_bb_per_100: list[list[float]]  # [match][name]
    faults: list[int]  # hands in which the player faulted
    first_faults: list[str | None]
    max_move_seconds: list[float]


@dataclass
class Standing:
    name: str
    hands: int
    bb_per_100: float
    ci95: float


def score(logs: list[MatchLog]) -> MatchResult:
    """
    Scores duplicate matches between the same players; the lists follow
    the first log's names. One sample per deck: what a player won with
    it over every seat, in big blinds per hand.
    """
    if not logs:
        raise ValueError("no matches to score")
    names = logs[0].names
    for log in logs:
        if log.config.mode != "duplicate":
            raise ValueError("only duplicate matches can be scored")
        if set(log.names) != set(names):
            raise ValueError(f"players {log.names} differ from {names}")

    samples: dict[str, list[float]] = {name: [] for name in names}
    match_bb_per_100 = []
    faults = {name: 0 for name in names}
    first_faults: dict[str, str | None] = {name: None for name in names}
    max_move_seconds = {name: 0.0 for name in names}
    for log in logs:
        values = _deal_values(log)
        for name in names:
            samples[name] += values[name]
            seconds = log.max_move_seconds.get(name, 0.0)
            max_move_seconds[name] = max(max_move_seconds[name], seconds)
        match_bb_per_100.append(
            [100 * mean_and_se(values[name])[0] for name in names]
        )
        for hand in log.hands:
            for seat, reason in hand.faults.items():
                name = hand.seats[seat]
                faults[name] += 1
                if first_faults[name] is None:
                    first_faults[name] = reason

    bb_per_100 = []
    half_widths = []
    for name in names:
        mean, se = mean_and_se(samples[name])
        bb_per_100.append(100 * mean)
        half_widths.append(100 * ci95(se, len(samples[name])))
    return MatchResult(
        names=list(names),
        hands=sum(len(log.hands) for log in logs),
        matches=len(logs),
        bb_per_100=bb_per_100,
        ci95=half_widths,
        match_bb_per_100=match_bb_per_100,
        faults=[faults[name] for name in names],
        first_faults=[first_faults[name] for name in names],
        max_move_seconds=[max_move_seconds[name] for name in names],
    )


def leaderboard(logs: list[MatchLog]) -> list[Standing]:
    """Every player of the duplicate matches in logs (tournaments are
    skipped), best first, scored as in score and pooled across matches."""
    runs: dict[str, list] = {}  # name: (samples, mean, se) per match
    hands: dict[str, int] = {}
    for log in logs:
        if log.config.mode != "duplicate":
            continue
        for name, values in _deal_values(log).items():
            mean, se = mean_and_se(values)
            runs.setdefault(name, []).append((len(values), mean, se))
            hands[name] = hands.get(name, 0) + sum(
                name in hand.seats for hand in log.hands
            )
    standings = []
    for name, name_runs in runs.items():
        count, mean, se = pool(name_runs)
        standings.append(
            Standing(name, hands[name], 100 * mean, 100 * ci95(se, count))
        )
    standings.sort(key=lambda s: (-s.bb_per_100, s.name))
    return standings


def _deal_values(log: MatchLog) -> dict[str, list[float]]:
    """Per player, one value per deck: its net over every hand played
    with that deck (one per rotation), in big blinds per hand."""
    count = len(log.names)
    net = {name: [0] * log.config.deals for name in log.names}
    for hand in log.hands:
        for seat, name in enumerate(hand.seats):
            net[name][hand.deal] += hand.net[seat]
    return {
        name: [chips / count / log.config.big_blind for chips in totals]
        for name, totals in net.items()
    }

import json
import os
import tempfile
from dataclasses import asdict

from arena.match import HandLog, MatchConfig, MatchLog


class ResultStore:
    """Every match played, one JSON line per MatchLog, append only."""

    def __init__(self, path: str):
        self.path = path

    def add(self, log: MatchLog) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(asdict(log)) + "\n")

    def logs(self) -> list[MatchLog]:
        if not os.path.exists(self.path):
            return []
        logs = []
        with open(self.path, encoding="utf-8") as file:
            for number, line in enumerate(file, 1):
                try:
                    logs.append(_decode(json.loads(line)))
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    raise ValueError(
                        f"{self.path}: line {number} is not a MatchLog: {e}"
                    ) from e
        return logs


def _decode(data: dict) -> MatchLog:
    """The MatchLog that asdict turned into data, JSON types undone: the
    launcher is a tuple again and fault seats are ints."""
    config = data["config"]
    hands = [
        HandLog(**{
            **hand,
            "faults": {int(seat): f for seat, f in hand["faults"].items()},
        })
        for hand in data["hands"]
    ]
    return MatchLog(**{
        **data,
        "config": MatchConfig(
            **{**config, "launcher": tuple(config["launcher"])}
        ),
        "hands": hands,
    })


def load_bankroll(path: str) -> dict[str, int]:
    """Team name -> chips, from a JSON file; {} if it does not exist."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as file:
        chips = json.load(file)
    if not isinstance(chips, dict) or not all(
        isinstance(name, str) and type(count) is int and count >= 0
        for name, count in chips.items()
    ):
        raise ValueError(
            f"{path}: expected an object of team name -> chips (int >= 0)"
        )
    return chips


def save_bankroll(path: str, chips: dict[str, int]) -> None:
    """Overwrites the file atomically (write a temp file, then rename)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(chips, file)
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise

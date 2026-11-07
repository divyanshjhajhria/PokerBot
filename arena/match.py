import hashlib
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from arena.players import Player, build
from arena.remote import TIME_LIMIT
from card_utils import full_deck
from engine import HandResult, play_hand


@dataclass(frozen=True)
class MatchConfig:
    deals: int  # duplicate: decks per rotation; tournament: max hands
    mode: Literal["duplicate", "tournament"] = "duplicate"
    seed: int = 0
    stack: int = 500  # starting stack for everyone
    stacks: dict[str, int] | None = None  # tournament: per team, overrides
    small_blind: int = 10
    big_blind: int = 20
    timeout: float = TIME_LIMIT
    launcher: tuple[str, ...] = ()  # e.g. ("bwrap", ...), prepended
    memory_mb: int = 2048
    checkpoint_dir: str | None = "checkpoints"  # tournament; None: off


@dataclass
class HandLog:
    rotation: int  # duplicate: seat rotation; tournament: 0
    deal: int  # duplicate: deck index; tournament: hand number
    seats: list[str]  # name in each seat
    net: list[int]  # per seat
    faults: dict[int, str]  # per seat


@dataclass
class HandRecord:
    """One played hand in full, for whoever wants to replay it. Handed to
    the on_hand callback of play_match; never part of a MatchLog."""

    rotation: int
    deal: int
    seats: list[str]  # name in each seat
    stacks: list[int]  # chips behind at the start, per seat
    button: int  # seat
    small_blind: int
    big_blind: int
    result: HandResult  # actions, hole cards, board, net, faults


OnHand = Callable[[HandRecord], None]


@dataclass
class MatchLog:
    names: list[str]
    config: MatchConfig
    hands: list[HandLog]
    max_move_seconds: dict[str, float]
    final_stacks: dict[str, int] | None = None  # tournament only
    checkpoints: dict[str, str] = field(default_factory=dict)  # name: path


def play_match(players: list[Player], config: MatchConfig,
               on_hand: OnHand | None = None) -> MatchLog:
    """
    Duplicate mode plays every deal once per seat rotation, so every
    player sits in every seat with every deck, and stacks reset each hand.
    Tournament mode plays up to config.deals hands with stacks carried
    over, until one player has every chip, then checkpoints the models.

    on_hand, if given, is called with a HandRecord after every hand (to
    record replays); it does not change the match.
    """
    _validate(players, config)
    names = [p.name for p in players]
    log = MatchLog(names, config, [], {name: 0.0 for name in names})
    if config.mode == "duplicate":
        _play_duplicate(players, config, log, on_hand)
    else:
        _play_tournament(players, config, log, on_hand)
    return log


def repeat_match(players: list[Player], config: MatchConfig,
                 times: int) -> list[MatchLog]:
    """times matches, with seeds config.seed, config.seed + 1, ..."""
    if times < 1:
        raise ValueError(f"times {times} must be >= 1")
    return [
        play_match(players, replace(config, seed=config.seed + i))
        for i in range(times)
    ]


def _validate(players: list[Player], config: MatchConfig) -> None:
    """Every input is checked before any model is built."""
    count = len(players)
    if not 2 <= count <= 9:
        raise ValueError(f"a match needs 2 to 9 players, not {count}")
    names = [p.name for p in players]
    for name in names:
        # Names become checkpoint file names.
        if (not isinstance(name, str) or name in ("", ".", "..")
                or "/" in name or "\\" in name):
            raise ValueError(f"invalid player name {name!r}")
    if len(set(names)) != count:
        raise ValueError(f"player names must be unique: {names}")
    if config.deals < 1:
        raise ValueError(f"deals {config.deals} must be >= 1")
    if not 1 <= config.small_blind <= config.big_blind:
        raise ValueError(
            f"blinds must satisfy 1 <= small_blind {config.small_blind} "
            f"<= big_blind {config.big_blind}"
        )
    if config.mode == "duplicate":
        if config.stacks is not None:
            raise ValueError("stacks is for tournaments only")
        if config.stack < config.big_blind:
            raise ValueError(
                f"stack {config.stack} is less than big_blind "
                f"{config.big_blind}"
            )
    elif config.mode == "tournament":
        if config.stacks is not None and set(config.stacks) != set(names):
            raise ValueError(
                f"stacks are for {sorted(config.stacks)}, but the players "
                f"are {sorted(names)}"
            )
        for name, chips in _starting_stacks(names, config).items():
            if chips < 1:
                raise ValueError(f"{name} starts with {chips} chips")
    else:
        raise ValueError(f"unknown mode {config.mode!r}")


def _starting_stacks(names: list[str], config: MatchConfig) -> dict:
    if config.stacks is not None:
        return {name: config.stacks[name] for name in names}
    return {name: config.stack for name in names}


def _build(p: Player, config: MatchConfig) -> "_Timed":
    # The same seed for p all match, so duplicate rotations stay paired.
    digest = hashlib.sha256(f"{config.seed}:{p.name}".encode()).digest()
    seed = int.from_bytes(digest[:8], "big")
    return _Timed(
        build(p, seed, config.timeout, config.launcher, config.memory_mb)
    )


def _deck(key: str) -> list:
    deck = full_deck()
    random.Random(key).shuffle(deck)
    return deck


def _play_duplicate(players, config, log, on_hand=None) -> None:
    count = len(players)
    order = [p.name for p in players]
    if count >= 3:
        random.Random(f"{config.seed}:seats").shuffle(order)
    for k in range(count):
        # Seat i is played by order[(i + k) % count].
        seated = [order[(i + k) % count] for i in range(count)]
        timed: dict[str, _Timed] = {}
        try:
            for p in players:
                timed[p.name] = _build(p, config)
            for d in range(config.deals):
                result = play_hand(
                    [timed[name] for name in seated],
                    _deck(f"{config.seed}:{d}"),
                    [config.stack] * count,
                    0,
                    config.small_blind,
                    config.big_blind,
                    seated,
                )
                log.hands.append(
                    HandLog(k, d, seated, result.net, result.faults)
                )
                if on_hand is not None:
                    on_hand(HandRecord(
                        k, d, list(seated), [config.stack] * count, 0,
                        config.small_blind, config.big_blind, result,
                    ))
        except BaseException:
            _close(timed.values())  # the error already raised wins
            raise
        _record_seconds(timed, log)
        error = _close(timed.values())
        if error is not None:
            raise error


def _play_tournament(players, config, log, on_hand=None) -> None:
    names = [p.name for p in players]
    stacks = _starting_stacks(names, config)
    timed: dict[str, _Timed] = {}
    try:
        for p in players:
            timed[p.name] = _build(p, config)
        button = None  # name of the last hand's button player
        for h in range(config.deals):
            seated = [name for name in names if stacks[name] > 0]
            if len(seated) < 2:
                break
            if button is None:
                button = seated[0]
            else:
                # The next player after the last button who still has
                # chips, in seat order.
                start = names.index(button)
                button = next(
                    names[(start + step) % len(names)]
                    for step in range(1, len(names) + 1)
                    if stacks[names[(start + step) % len(names)]] > 0
                )
            result = play_hand(
                [timed[name] for name in seated],
                _deck(f"{config.seed}:t:{h}"),
                [stacks[name] for name in seated],
                seated.index(button),
                config.small_blind,
                config.big_blind,
                seated,
            )
            before = [stacks[name] for name in seated]
            for seat, name in enumerate(seated):
                stacks[name] += result.net[seat]
            log.hands.append(HandLog(0, h, seated, result.net, result.faults))
            if on_hand is not None:
                on_hand(HandRecord(
                    0, h, list(seated), before, seated.index(button),
                    config.small_blind, config.big_blind, result,
                ))
        log.final_stacks = stacks
        if config.checkpoint_dir is not None:
            for name in names:
                path = _checkpoint(timed[name], config.checkpoint_dir, name)
                if path is not None:
                    log.checkpoints[name] = path
    except BaseException:
        _close(timed.values())  # the error already raised wins
        raise
    _record_seconds(timed, log)
    error = _close(timed.values())
    if error is not None:
        raise error


def _checkpoint(proxy: "_Timed", folder: str, name: str) -> str | None:
    """Writes the model's checkpoint to folder/name.pkl and returns the
    path; None if the model has no checkpoint or it raised."""
    checkpoint = getattr(proxy, "checkpoint", None)
    if checkpoint is None:
        return None
    try:
        data = checkpoint()
    except Exception:
        return None
    if not isinstance(data, bytes):
        return None
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".pkl")
    # A crash never leaves half a file at path.
    temp = path + ".tmp"
    with open(temp, "wb") as file:
        file.write(data)
    os.replace(temp, path)
    return path


def _record_seconds(timed: dict, log: MatchLog) -> None:
    for name, proxy in timed.items():
        seconds = max(log.max_move_seconds[name], proxy.max_seconds)
        log.max_move_seconds[name] = seconds


class _Timed:
    """Passes seed and run through to a model, timing each run call, and
    end_game and checkpoint too when the model has them."""

    def __init__(self, model):
        self.model = model
        self.max_seconds = 0.0

    def seed(self, *args, **kwargs):
        return self.model.seed(*args, **kwargs)

    def run(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            return self.model.run(*args, **kwargs)
        finally:
            seconds = time.perf_counter() - start
            self.max_seconds = max(self.max_seconds, seconds)

    def __getattr__(self, name):
        # Only runs for names not set on the proxy: these are the model's
        # own, so the proxy has one exactly when the model does.
        if name in ("end_game", "checkpoint"):
            method = getattr(self.model, name)
            if callable(method):
                return method
        raise AttributeError(f"{type(self).__name__} has no {name!r}")


def _close(proxies) -> Exception | None:
    """Calls close() on every model that has one; returns the first error
    instead of raising it, so every model still gets closed."""
    first = None
    for proxy in proxies:
        try:
            close = getattr(proxy.model, "close", None)
            if close is not None:
                close()
        except Exception as error:
            if first is None:
                first = error
    return first

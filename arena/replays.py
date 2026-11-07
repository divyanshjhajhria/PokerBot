"""
Replays: one played hand, in the compact form the results site plays back.

A HandRecord (arena.match) has every action the engine saw. The site wants
each action as [seat, name, amount, street, fault] with the chips a call or
bet ADDS and the TOTAL for a raise, the way a person reads a hand, so the
hand is replayed through a GameState to work those amounts out. Hole cards
are published only for a hand that reached a showdown.
"""

import gzip
import json
import os

from arena.match import HandRecord
from card_utils import CATEGORIES, evaluate
from game_state import GameState
from player_action import DEALER

SCHEMA = 1

HAND_NAMES = {
    "high_card": "High Card",
    "pair": "One Pair",
    "two_pair": "Two Pair",
    "three_of_a_kind": "Three of a Kind",
    "straight": "Straight",
    "flush": "Flush",
    "full_house": "Full House",
    "four_of_a_kind": "Four of a Kind",
    "straight_flush": "Straight Flush",
}


def site_hand(record: HandRecord, index: int) -> dict:
    """The public, compact form of one hand (index: its number in the
    pairing, from 0)."""
    result = record.result
    count = len(record.seats)
    state = GameState(list(record.stacks))
    state.setup_hand(
        list(record.stacks), record.button, record.small_blind,
        record.big_blind, list(record.seats),
    )
    small = record.button if count == 2 else (record.button + 1) % count
    big = (small + 1) % count
    actions = [
        [small, "post_small_blind", state.bets[small], "preflop", 0],
        [big, "post_big_blind", state.bets[big], "preflop", 0],
    ]
    won = [0] * count
    shown: list[int] = []
    for action in result.actions:
        if action.kind == "win":
            won[action.player_id] += action.amount
        elif action.kind == "reveal_card":
            if action.player_id != DEALER:
                if action.player_id not in shown:
                    shown.append(action.player_id)
            else:
                state.process_action(action)
        else:
            seat = action.player_id
            street = state.street
            if action.kind == "fold":
                name, amount = "fold", 0
            elif action.kind == "call":
                owed = min(state.call_amount, state.stacks[seat])
                name, amount = ("call", owed) if owed else ("check", 0)
            elif state.current_bet == 0:
                name, amount = "bet", action.amount - state.bets[seat]
            else:
                name, amount = "raise_to", action.amount
            actions.append([seat, name, amount, street, 0])
            state.process_action(action)

    showdown = bool(shown)
    board = [str(card) for card in result.board]
    results = []
    for seat in range(count):
        label = ""
        if seat in shown:
            rank = evaluate(list(result.hands[seat]) + list(result.board))
            label = HAND_NAMES[CATEGORIES[rank[0]]]
        results.append([won[seat], label])
    return {
        "n": index,
        "deal": record.deal,
        "o": record.rotation,
        "seats": list(record.seats),
        "stacks": list(record.stacks),
        "sb": record.small_blind,
        "bb": record.big_blind,
        "dealer": record.button,
        "hole": (
            [[str(card) for card in hand] for hand in result.hands]
            if showdown else None
        ),
        "board": board,
        "actions": actions,
        "end": "showdown" if showdown else "fold",
        "res": results,
        "net": list(result.net),
    }


def select_hands(hands: list[dict], limit: int) -> list[dict]:
    """The limit hands with the biggest chip swings, in play order; limit 0
    keeps every hand."""
    if limit <= 0 or len(hands) <= limit:
        return list(hands)
    ranked = sorted(
        hands,
        key=lambda h: (-max(abs(n) for n in h["net"]),
                       h["end"] != "showdown", h["n"]),
    )
    return sorted(ranked[:limit], key=lambda h: h["n"])


def replay_file(pair_id: str, a: str, b: str, hands: list[dict],
                limit: int = 60) -> dict:
    """The public replay of one pairing."""
    chosen = select_hands(hands, limit)
    return {
        "schema": SCHEMA,
        "id": pair_id,
        "kind": "group",
        "a": a,
        "b": b,
        "reveal": "showdown",
        "total_hands": len(hands),
        "shown": len(chosen),
        "selection": "all" if len(chosen) == len(hands) else "swings",
        "bb": hands[0]["bb"] if hands else 0,
        "hands": chosen,
    }


# -- keeping every hand of a pairing on disk -------------------------------


def save_hands(path: str, hands: list[dict]) -> None:
    """Writes hands (site_hand dicts) as gzipped JSON lines, atomically."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = path + ".tmp"
    with gzip.open(temp, "wt", encoding="utf-8") as file:
        for hand in hands:
            file.write(json.dumps(hand) + "\n")
    os.replace(temp, path)


def load_hands(path: str) -> list[dict]:
    """The hands save_hands wrote; [] if the file is missing or damaged."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            return [json.loads(line) for line in file if line.strip()]
    except (OSError, EOFError, ValueError):
        return []

"""Test dummies, oracles and log builders for the arena tests."""

import math
import os
import random
import statistics
import time

from card_utils import full_deck
from engine import play_hand
from player_action import PlayerAction as A

INF = math.inf
BUILT = []  # every dummy built, in order
LOG = []  # (event, name, instance index within its name)

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
       7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
       13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
       19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064,
       25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}


def t95_ref(df):
    return INF if df < 1 else T95.get(df, 1.96)


def mse_ref(values):
    if not values:
        return None, INF
    if len(values) == 1:
        return float(values[0]), INF
    return (statistics.fmean(values),
            statistics.stdev(values) / math.sqrt(len(values)))


def reset():
    BUILT.clear()
    LOG.clear()


class Boom(BaseException):
    pass


class CtorError(Exception):
    pass


class CloseError(Exception):
    pass


class CkptError(Exception):
    pass


def named(name, base):
    """A per-player subclass so instances know their player name."""
    return type(f"{base.__name__}_{name}", (base,),
                {"NAME": name, "COUNT": 0})


class Rec:
    """Always calls; records everything."""

    NAME = "?"
    COUNT = 0
    FAIL_AT = None  # construction index (per name) that raises

    def __init__(self, seed=None, register=True):
        cls = type(self)
        self.index = cls.COUNT
        cls.COUNT += 1
        if register:
            LOG.append(("build", self.NAME, self.index))
        if self.FAIL_AT is not None and self.index == self.FAIL_AT:
            raise CtorError(self.NAME)
        self.ctor_seed = seed
        self.seeds = []
        self.runs = []
        self.closed = 0
        self.checkpoints = 0
        self.player_id = None
        self.hand = None
        if register:
            BUILT.append(self)

    def seed(self, hand, player_id):
        self.seeds.append((list(hand), player_id))
        self.player_id = player_id
        self.hand = list(hand)
        LOG.append(("seed", self.NAME, self.index))

    def run(self, actions, gs):
        self.runs.append({"hand": len(self.seeds) - 1,
                          "actions": list(actions), "teams": tuple(gs.teams),
                          "button": gs.button, "sb": gs.small_blind,
                          "bb": gs.big_blind, "stacks": tuple(gs.stacks),
                          "pot": gs.pot, "view": type(gs).__name__})
        return self.decide(gs)

    def decide(self, gs):
        return A.call(self.player_id)

    def close(self):
        self.closed += 1
        LOG.append(("close", self.NAME, self.index))


class Folder(Rec):
    def decide(self, gs):
        return A.fold(self.player_id)


class Shover(Rec):
    def decide(self, gs):
        if gs.can_raise:
            return A.raise_to(self.player_id, gs.max_raise_to)
        return A.call(self.player_id)


class CardBot(Rec):
    def decide(self, gs):
        a, b = self.hand
        me = self.player_id
        if a.rank == b.rank and gs.can_raise:
            return A.raise_to(me, gs.min_raise_to)
        if "A" in (a.rank, b.rank) or gs.call_amount == 0:
            return A.call(me)
        return A.fold(me)


class SometimesFaults(CardBot):
    def decide(self, gs):
        if self.hand[0].suit == "H":
            raise ValueError(f"heart {self.hand[0]}{self.hand[1]}")
        return super().decide(gs)


class SeedRaiser(Rec):
    def seed(self, hand, player_id):
        super().seed(hand, player_id)
        raise RuntimeError("seed fails")


class RunRaiser(Rec):
    def decide(self, gs):
        raise ValueError("run fails")


class Oddball(Rec):
    def decide(self, gs):
        return None if len(self.runs) % 2 else "call"


class Sleeper(Rec):
    def decide(self, gs):
        if len(self.runs) == 1:
            time.sleep(0.03)
        return A.call(self.player_id)


class SlowRaiser(Rec):
    def decide(self, gs):
        time.sleep(0.03)
        raise ValueError("slow then raise")


class Booms(Rec):
    def decide(self, gs):
        raise Boom()


class CtorRaiser(Rec):
    FAIL_AT = 1  # second construction (rotation 1)


class CloseRaiser(Rec):
    def close(self):
        super().close()
        raise CloseError(self.NAME)


class LateCloseRaiser(Rec):
    def close(self):
        super().close()
        if self.index >= 1:
            raise CloseError(self.NAME)


class BoomCloseRaiser(Booms):
    def close(self):
        super().close()
        raise CloseError(self.NAME)


class EndRec(Rec):
    def __init__(self, seed=None, register=True):
        super().__init__(seed, register)
        self.ends = []

    def end_game(self, actions, gs):
        self.ends.append(list(actions))


class EndRaiser(Rec):
    def end_game(self, actions, gs):
        raise ValueError("end fails")


class Ckpt(Rec):
    def checkpoint(self):
        self.checkpoints += 1
        LOG.append(("checkpoint", self.NAME, self.index))
        return f"ckpt-{self.NAME}-{len(self.seeds)}".encode()


class CkptRaiser(Rec):
    def checkpoint(self):
        LOG.append(("checkpoint", self.NAME, self.index))
        raise CkptError(self.NAME)


class CkptBoom(Rec):
    def checkpoint(self):
        raise Boom()


class ShoverCkpt(Ckpt, Shover):
    pass


class Bare:
    """Only seed and run (built through DUMMIES with seed=)."""

    __slots__ = ("player_id",)

    def __init__(self, seed=None):
        pass

    def seed(self, hand, player_id):
        self.player_id = player_id

    def run(self, actions, gs):
        return A.call(self.player_id)


# -- oracles -------------------------------------------------------------------

def seat_order(names, seed):
    order = list(names)
    if len(order) >= 3:
        random.Random(f"{seed}:seats").shuffle(order)
    return order


def deck_dup(seed, d):
    deck = full_deck()
    random.Random(f"{seed}:{d}").shuffle(deck)
    return deck


def deck_tour(seed, h):
    deck = full_deck()
    random.Random(f"{seed}:t:{h}").shuffle(deck)
    return deck


def oracle_duplicate(bases, deals, seed=0, stack=500, sb=10, bb=20):
    """bases: name -> class (in given order). Returns a list of
    (rotation, deal, seats, net, faults)."""
    names = list(bases)
    n = len(names)
    order = seat_order(names, seed)
    out = []
    for k in range(n):
        models = {nm: named(nm, bases[nm])(register=False) for nm in names}
        seated = [order[(i + k) % n] for i in range(n)]
        for d in range(deals):
            res = play_hand([models[s] for s in seated], deck_dup(seed, d),
                            [stack] * n, 0, sb, bb, teams=seated)
            out.append((k, d, seated, res.net, res.faults))
    return out


def next_button(order, chips, prev):
    i = order.index(prev)
    for step in range(1, len(order) + 1):
        cand = order[(i + step) % len(order)]
        if chips[cand] > 0:
            return cand
    raise AssertionError


def oracle_tournament(bases, deals, seed=0, stack=500, stacks=None, sb=10,
                      bb=20):
    """Returns (hand logs, final stacks, buttons (names), bust events)."""
    order = list(bases)
    chips = {nm: (stacks[nm] if stacks else stack) for nm in order}
    models = {nm: named(nm, bases[nm])(register=False) for nm in order}
    out, buttons = [], []
    button = None
    for h in range(deals):
        seated = [nm for nm in order if chips[nm] > 0]
        if len(seated) < 2:
            break
        button = seated[0] if button is None else next_button(
            order, chips, button)
        buttons.append(button)
        res = play_hand([models[s] for s in seated], deck_tour(seed, h),
                        [chips[s] for s in seated], seated.index(button),
                        sb, bb, teams=seated)
        for s, d in zip(seated, res.net):
            chips[s] += d
        out.append((0, h, seated, res.net, res.faults))
    return out, chips, buttons


def hand_tuples(log):
    return [(h.rotation, h.deal, list(h.seats), list(h.net), dict(h.faults))
            for h in log.hands]


# -- process helpers -----------------------------------------------------------

def alive(pid):
    if os.name == "nt":
        # os.kill(pid, 0) would terminate the process on Windows.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def wait_dead(pid, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.02)
    return not alive(pid)


def read_pids(folder):
    path = os.path.join(folder, "pids.txt")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [int(x) for x in f.read().split()]


# -- synthetic logs ------------------------------------------------------------

def mk_log(names, hands, mode="duplicate", bb=20, deals=None, mms=None,
           final_stacks=None, checkpoints=None, **cfg):
    from arena.match import HandLog, MatchConfig, MatchLog

    hand_logs = [HandLog(r, d, list(s), list(net), dict(f))
                 for r, d, s, net, f in hands]
    config = MatchConfig(
        deals=deals or (max((h[1] for h in hands), default=0) + 1),
        mode=mode, big_blind=bb, small_blind=min(10, bb), **cfg)
    return MatchLog(list(names), config, hand_logs,
                    dict(mms or {nm: 0.0 for nm in names}),
                    final_stacks, dict(checkpoints or {}))


def score_oracle(logs):
    names = list(logs[0].names)
    values = {nm: [] for nm in names}
    match_rows = []
    faults = {nm: 0 for nm in names}
    first = {nm: None for nm in names}
    mms = {nm: 0.0 for nm in names}
    hands = 0
    for log in logs:
        n = len(log.names)
        bb = log.config.big_blind
        per = {}
        for h in log.hands:
            hands += 1
            for i, s in enumerate(h.seats):
                per[(h.deal, s)] = per.get((h.deal, s), 0) + h.net[i]
                if i in h.faults:
                    faults[s] += 1
                    if first[s] is None:
                        first[s] = h.faults[i]
        deals = sorted({h.deal for h in log.hands})
        row = []
        for nm in names:
            vals = [per.get((d, nm), 0) / n / bb for d in deals]
            values[nm].extend(vals)
            row.append(100 * statistics.fmean(vals))
        match_rows.append(row)
        for nm in names:
            mms[nm] = max(mms[nm], log.max_move_seconds.get(nm, 0.0))
    bb100, ci = [], []
    for nm in names:
        mean, se = mse_ref(values[nm])
        bb100.append(100 * mean)
        cnt = len(values[nm])
        ci.append(INF if cnt < 2 else 100 * t95_ref(cnt - 1) * se)
    return dict(names=names, hands=hands, matches=len(logs),
                bb_per_100=bb100, ci95=ci, match_bb_per_100=match_rows,
                faults=[faults[nm] for nm in names],
                first_faults=[first[nm] for nm in names],
                max_move_seconds=[mms[nm] for nm in names])


def leaderboard_oracle(logs):
    vals, hands = {}, {}
    for log in logs:
        if log.config.mode != "duplicate":
            continue
        n = len(log.names)
        bb = log.config.big_blind
        per = {}
        for h in log.hands:
            for i, s in enumerate(h.seats):
                per[(h.deal, s)] = per.get((h.deal, s), 0) + h.net[i]
                hands[s] = hands.get(s, 0) + 1
        deals = sorted({h.deal for h in log.hands})
        for nm in log.names:
            vals.setdefault(nm, []).extend(
                per.get((d, nm), 0) / n / bb for d in deals)
    rows = []
    for nm, v in vals.items():
        mean, se = mse_ref(v)
        ci = INF if len(v) < 2 else 100 * t95_ref(len(v) - 1) * se
        rows.append((nm, hands.get(nm, 0), 100 * mean, ci))
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows

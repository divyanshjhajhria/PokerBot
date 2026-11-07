"""
Heads-up no-limit hold'em bot for the GUH poker arena.

Design (25 bb deep, stacks reset every hand, duplicate deals, scored by
bb/100): maximise chip EV per hand, no survival concerns. Equity-driven
decisions (precomputed preflop tables + Monte Carlo postflop) with an
opponent profile learned within the rotation (fold-to-raise, fold-to-bet,
jam frequency, VPIP/PFR) that switches the bot between exploit modes:
steal vs folders, value-bomb vs stations, snap-call vs jammers.
"""
import random
import time

from card_utils import Card, evaluate, full_deck
from player_action import DEALER, PlayerAction
from preflop_tables import PCT, TOPS, VS_RANDOM, VS_TOP

RANKS = "23456789TJQKA"


def hand_key(cards):
    a, b = cards[0], cards[1]
    if a.value < b.value:
        a, b = b, a
    if a.rank == b.rank:
        return a.rank + b.rank
    return a.rank + b.rank + ("s" if a.suit == b.suit else "o")


def _rate(k, n, prior, weight):
    """Beta-smoothed rate: prior until enough observations."""
    return (k + prior * weight) / (n + weight)


def _new_stats():
    return {
        "hands": 0,
        # preflop
        "pre_opp": 0, "vpip": 0, "pfr": 0,
        "open_opp": 0, "open_raise": 0, "open_jam": 0, "open_limp": 0,
        "open_fold": 0,
        "faced_raise": 0, "fold_to_raise": 0, "call_raise": 0,
        "reraise": 0, "jam_over_raise": 0,
        "faced_3bet": 0, "fold_to_3bet": 0,
        # postflop
        "bet_opp": 0, "bet_n": 0, "jam_post": 0,
        "faced_bet": 0, "fold_to_bet": 0, "call_bet": 0, "raise_bet": 0,
        "faced_small": 0, "fold_small": 0, "faced_big": 0, "fold_big": 0,
        # showdown
        "showdowns": 0, "sd_aggr": 0, "sd_aggr_weak": 0,
        # sizing: random bots raise random amounts (non-all-in overbets)
        "raises": 0, "overbets": 0,
        # preflop fold rate by what they face: a small raise (<= 3 bb) or
        # an all-in; and the hands that called our jams (percentiles)
        "faced_small_raise": 0, "fold_small_raise": 0,
        "faced_jam": 0, "fold_jam": 0,
        "jamcall_n": 0, "jamcall_pct_sum": 0.0,
        # percentiles of hands shown at showdown, by their preflop action
        "sd_pct_raised": [], "sd_pct_called": [],
    }


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None
        self.rng = random.Random(20261107)
        self.opp = {}  # team name -> stats dict
        self.vs_random = dict(VS_RANDOM)
        self.pct = dict(PCT)
        self.tops = list(TOPS)
        self.vs_top = {int(t): dict(d) for t, d in VS_TOP.items()}
        # Every hole-card combo with its key, for range sampling.
        deck = full_deck()
        self.combos = []
        for i in range(len(deck)):
            for j in range(i + 1, len(deck)):
                self.combos.append((deck[i], deck[j], hand_key([deck[i], deck[j]])))
        self.mc_samples = 320
        self.mc_time_cap = 0.3   # seconds per Monte Carlo call; samples cap too
        self.mc_pool_cap = 300   # villain combos kept for ranking and sampling
        self.cur = {}

    # ------------------------------------------------------------------ hand
    def seed(self, hand, player_id):
        self.hand = list(hand)
        self.player_id = player_id
        self.cur = {
            "ready": False, "street": "preflop", "board_n": 0,
            "bets": {}, "max": 0, "committed": {}, "start": {},
            "folded": set(), "allin": set(), "aggressor": None,
            "pre_raises": 0, "last_aggr": {}, "shown": {}, "opp": None,
            "we_raised_pre": False, "street_bets_opened": False,
            "we_jammed_pre": False, "opp_called_jam": False,
            "opp_pre_raised": False,
            "opp_last_post": None,
            # The opponent's postflop line, per street, and each street's
            # hand-strength ranking of every combo (built once per hand).
            "opp_line": {"flop": [], "turn": [], "river": []},
            "rank_cache": {},
        }

    def _setup(self, gs):
        c = self.cur
        if c["ready"]:
            return
        n = gs.num_players
        me = self.player_id
        c["opp"] = [p for p in range(n) if p != me][0] if n == 2 else None
        for p in range(n):
            team = gs.teams[p]
            if p != me and team not in self.opp:
                self.opp[team] = _new_stats()
            c["start"][p] = gs.stacks[p] + gs.bets[p]
            c["committed"][p] = 0
        c["bets"] = {p: 0 for p in range(n)}
        sb = gs.button if n == 2 else (gs.button + 1) % n
        bb = (sb + 1) % n
        c["bets"][sb] = min(gs.small_blind, c["start"][sb])
        c["bets"][bb] = min(gs.big_blind, c["start"][bb])
        c["max"] = max(c["bets"].values())
        c["sb"], c["bb"] = sb, bb
        c["ready"] = True

    def _stats(self, gs, p):
        return self.opp[gs.teams[p]]

    def _is_allin(self, p, total):
        c = self.cur
        return c["committed"].get(p, 0) + total >= c["start"].get(p, 500)

    def _ingest(self, actions, gs):
        """Replay actions since our last turn to learn about opponents."""
        self._setup(gs)
        c = self.cur
        me = self.player_id
        for a in actions:
            if a.player_id == DEALER:
                c["board_n"] += 1
                if c["board_n"] in (3, 4, 5):
                    for p in c["bets"]:
                        c["committed"][p] += c["bets"][p]
                        c["bets"][p] = 0
                    c["max"] = 0
                    c["street"] = {3: "flop", 4: "turn", 5: "river"}[c["board_n"]]
                    c["aggressor"] = None
                continue
            p = a.player_id
            if a.kind == "reveal_card":
                c["shown"].setdefault(p, []).append(a.card)
                continue
            if a.kind == "win":
                continue
            facing = c["max"] > c["bets"].get(p, 0)
            pre = c["street"] == "preflop"
            if p != me:
                pot_now = sum(c["committed"].values()) + sum(c["bets"].values())
                bet_frac = (c["max"] - c["bets"].get(p, 0)) / max(1, pot_now - (c["max"] - c["bets"].get(p, 0)))
                self._learn(gs, p, a, facing, pre, bet_frac, pot_now)
            if a.kind == "fold":
                c["folded"].add(p)
            elif a.kind == "call":
                owed = c["max"] - c["bets"].get(p, 0)
                room = c["start"].get(p, 500) - c["committed"].get(p, 0) - c["bets"].get(p, 0)
                c["bets"][p] = c["bets"].get(p, 0) + min(owed, max(room, 0))
                if room <= owed:
                    c["allin"].add(p)
            elif a.kind == "raise":
                c["bets"][p] = a.amount
                c["max"] = max(c["max"], a.amount)
                c["aggressor"] = p
                c["last_aggr"][c["street"]] = p
                if pre:
                    c["pre_raises"] += 1
                    if p == me:
                        c["we_raised_pre"] = True
                if self._is_allin(p, a.amount):
                    c["allin"].add(p)
                    if pre and p == me:
                        c["we_jammed_pre"] = True

    def _learn(self, gs, p, a, facing, pre, bet_frac=0.0, pot_now=0):
        s = self._stats(gs, p)
        c = self.cur
        allin = a.kind == "raise" and self._is_allin(p, a.amount)
        if a.kind == "raise":
            s["raises"] += 1
            if not allin and a.amount - c["max"] > 2.0 * max(1, pot_now):
                s["overbets"] += 1
        if not pre:
            if a.kind == "raise":
                c["opp_last_post"] = "raise" if facing else "bet"
            elif a.kind == "call" and facing:
                c["opp_last_post"] = "call"
            if a.kind == "raise":
                kind = "raise" if facing else "bet"
            elif a.kind == "call":
                kind = "call" if facing else "check"
            else:
                kind = a.kind
            c["opp_line"][c["street"]].append(kind)
        if pre and a.kind == "raise":
            c["opp_pre_raised"] = True
        if pre:
            raised_before = c["max"] > gs.big_blind
            s["pre_opp"] += 1
            if a.kind in ("call", "raise") and (facing or a.kind == "raise"):
                s["vpip"] += 1
            if a.kind == "raise":
                s["pfr"] += 1
            if not raised_before:
                if facing or a.kind == "raise":  # SB open, or BB raising a limp
                    s["open_opp"] += 1
                    if a.kind == "raise":
                        s["open_raise"] += 1
                        if allin:
                            s["open_jam"] += 1
                    elif a.kind == "call":
                        s["open_limp"] += 1
                    else:
                        s["open_fold"] += 1
            else:
                if c["pre_raises"] >= 2:
                    s["faced_3bet"] += 1
                    if a.kind == "fold":
                        s["fold_to_3bet"] += 1
                s["faced_raise"] += 1
                if a.kind == "fold":
                    s["fold_to_raise"] += 1
                jam_faced = (c["aggressor"] is not None and c["aggressor"] != p
                             and c["aggressor"] in c["allin"])
                if jam_faced:
                    s["faced_jam"] += 1
                    if a.kind == "fold":
                        s["fold_jam"] += 1
                    elif a.kind == "call" and c["aggressor"] == self.player_id:
                        c["opp_called_jam"] = True
                elif c["max"] <= 3 * gs.big_blind:
                    s["faced_small_raise"] += 1
                    if a.kind == "fold":
                        s["fold_small_raise"] += 1
                elif a.kind == "call":
                    s["call_raise"] += 1
                else:
                    s["reraise"] += 1
                    if allin:
                        s["jam_over_raise"] += 1
        else:
            if not facing:
                s["bet_opp"] += 1
                if a.kind == "raise":
                    s["bet_n"] += 1
                    if allin:
                        s["jam_post"] += 1
            else:
                s["faced_bet"] += 1
                small = bet_frac <= 0.42
                s["faced_small" if small else "faced_big"] += 1
                if a.kind == "fold":
                    s["fold_to_bet"] += 1
                    s["fold_small" if small else "fold_big"] += 1
                elif a.kind == "call":
                    s["call_bet"] += 1
                else:
                    s["raise_bet"] += 1

    # ---------------------------------------------------------------- equity
    def _eq_top(self, key, x):
        """Preflop equity of key vs the top x% of hands (interpolated)."""
        x = max(self.tops[0], min(100, x))
        lo = max(t for t in self.tops if t <= x)
        hi = min(t for t in self.tops if t >= x)
        if lo == hi:
            return self.vs_top[lo][key]
        w = (x - lo) / (hi - lo)
        return self.vs_top[lo][key] * (1 - w) + self.vs_top[hi][key] * w

    def _mc_equity(self, board, top_pct, samples, bands=None, hist=None):
        """Postflop equity vs a villain range = top_pct% of preflop hands,
        by Monte Carlo over the unseen cards. bands = [(n, (lo, hi)), ...]
        narrows that range street by street: keep the villain hands whose
        made-hand strength on the first n board cards sits between those
        quantiles (0 weakest .. 1 strongest). Sampling stops at
        mc_time_cap seconds after the call starts (16 samples minimum)."""
        t_end = time.perf_counter() + self.mc_time_cap
        known = set(str(c) for c in self.hand) | set(str(c) for c in board)
        limit = top_pct / 100.0
        pool = [cb for cb in self.combos
                if self.pct[cb[2]] <= limit + 1e-9
                and str(cb[0]) not in known and str(cb[1]) not in known]
        if len(pool) < 20:
            pool = [cb for cb in self.combos
                    if str(cb[0]) not in known and str(cb[1]) not in known]
        if hist is not None:
            # Their shown-hand distribution replaces the top-X% guess.
            pool_all = [cb for cb in self.combos
                        if str(cb[0]) not in known and str(cb[1]) not in known]
            weights = [hist[min(9, int(self.pct[cb[2]] * 10))] for cb in pool_all]
            pool = self.rng.choices(pool_all, weights=weights, k=self.mc_pool_cap)
        elif len(pool) > self.mc_pool_cap:   # bounds ranking + sampling cost
            pool = self.rng.sample(pool, self.mc_pool_cap)
        for n_board, (lo, hi) in (bands or []):
            rank = self._ranking(board, n_board, pool)
            lo = max(0.0, min(0.95, lo))
            hi = max(lo + 0.05, min(1.0, hi))
            ranked = sorted(pool, key=lambda cb: rank[str(cb[0]) + str(cb[1])])
            n = len(ranked)
            sub = ranked[int(lo * n):max(int(lo * n) + 10, int(hi * n))]
            if len(sub) >= 10:
                pool = sub
        deck = [c for c in full_deck() if str(c) not in known]
        need = 5 - len(board)
        rng = self.rng
        won = 0.0
        done = 0
        for i in range(samples):
            if i >= 16 and not (i & 15) and time.perf_counter() > t_end:
                break
            done += 1
            v = pool[rng.randrange(len(pool))]
            rest = [c for c in deck if c is not v[0] and c is not v[1]
                    and str(c) != str(v[0]) and str(c) != str(v[1])]
            draw = rng.sample(rest, need) if need else []
            full = list(board) + draw
            mine = evaluate(self.hand + full)
            theirs = evaluate([v[0], v[1]] + full)
            if mine > theirs:
                won += 1
            elif mine == theirs:
                won += 0.5
        return won / done

    # --------------------------------------------------------------- helpers
    def _raise(self, gs, total):
        me = self.player_id
        if not gs.can_raise:
            return PlayerAction.call(me)
        low, high = gs.raise_range
        total = int(round(total))
        total = min(high, max(low, total))
        if total >= 0.72 * high:  # don't leave crumbs behind
            total = high
        return PlayerAction.raise_to(me, total)

    def _call(self):
        return PlayerAction.call(self.player_id)

    def _fold_or_check(self, gs):
        if gs.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def _profile(self, gs, opp):
        s = self._stats(gs, opp)
        # Field priors (step 2): what a hackathon field tends to do.
        f_pre = _rate(s["fold_to_raise"], s["faced_raise"], 0.55, 6)
        f_3b = _rate(s["fold_to_3bet"], s["faced_3bet"], 0.55, 4)
        jam_over = _rate(s["jam_over_raise"], s["faced_raise"], 0.03, 4)
        open_jam = _rate(s["open_jam"], s["open_opp"], 0.03, 5)
        open_raise = _rate(s["open_raise"], s["open_opp"], 0.5, 5)
        vpip = _rate(s["vpip"], s["pre_opp"], 0.5, 6)
        pfr = _rate(s["pfr"], s["pre_opp"], 0.25, 6)
        f_small = _rate(s["fold_small"], s["faced_small"], 0.3, 4)
        f_big = _rate(s["fold_big"], s["faced_big"], 0.5, 4)
        bet_rate = _rate(s["bet_n"], s["bet_opp"], 0.4, 6)
        jam_post = _rate(s["jam_post"], s["bet_opp"], 0.03, 6)
        raise_bet = _rate(s["raise_bet"], s["faced_bet"], 0.1, 6)
        f_small_raise = _rate(s["fold_small_raise"], s["faced_small_raise"], f_pre, 4)
        # Postflop folding starts from how they fold to small raises
        # preflop, so postflop exploits kick in before postflop data.
        f_post = _rate(s["fold_to_bet"], s["faced_bet"], f_small_raise, 4)
        f_jam = _rate(s["fold_jam"], s["faced_jam"], 0.6, 4)
        # If they call jams with their top W of hands, the hands they show
        # average W/2: so the width is twice the mean shown percentile.
        if s["jamcall_n"] >= 3:
            jam_call_w = min(1.0, 2.0 * s["jamcall_pct_sum"] / s["jamcall_n"])
        else:
            jam_call_w = 1.0 - f_jam
        n = s["hands"]
        return {
            "f_small_raise": f_small_raise, "f_jam": f_jam,
            "jam_call_w": max(0.05, jam_call_w),
            "f_pre": f_pre, "f_3b": f_3b, "jam_over": jam_over,
            "open_jam": open_jam, "open_raise": open_raise, "vpip": vpip,
            "pfr": pfr, "f_post": f_post, "bet_rate": bet_rate,
            "f_small": f_small, "f_big": f_big,
            "size_sensitive": (s["faced_small"] + s["faced_big"] >= 6
                               and f_big - f_small >= 0.3),
            "jam_post": jam_post, "raise_bet": raise_bet, "n": n,
            # labels
            "maniac": (open_jam > 0.35 or jam_over > 0.35 or jam_post > 0.35),
            "station": (s["faced_bet"] >= 6 and f_post < 0.18),
            "station_pre": (s["faced_raise"] >= 6 and f_pre < 0.25),
            "folder": (n >= 6 and f_post > 0.65 and f_pre > 0.6),
            "passive": (n >= 8 and bet_rate < 0.2 and pfr < 0.15),
            "aggro": (bet_rate > 0.6 or pfr > 0.6),
            # Loose with erratic sizing: their raises say nothing about
            # their cards, so play them as any two cards.
            "random": (vpip > 0.55 and s["raises"] >= 8
                       and s["overbets"] > 0.15 * s["raises"]),
        }

    # ------------------------------------------------------------------ run
    def run(self, actions, gs):
        try:
            self._ingest(actions, gs)
            return self._decide(gs)
        except Exception:
            return self._fold_or_check(gs)

    def _decide(self, gs):
        me = self.player_id
        c = self.cur
        opp = c["opp"]
        if opp is None:  # not heads-up: fall back to a plain equity line
            return self._multiway(gs)
        prof = self._profile(gs, opp)
        key = hand_key(self.hand)
        if gs.street == "preflop":
            return self._preflop(gs, opp, prof, key)
        return self._postflop(gs, opp, prof, key)

    # -------------------------------------------------------------- preflop
    def _jam_range_pct(self, prof, over_raise):
        rate = prof["jam_over"] if over_raise else prof["open_jam"]
        if prof["maniac"] or prof["random"] or rate >= 0.35:
            return 100.0
        return max(6.0, min(100.0, 110.0 * rate))

    def _ev_jam_preflop(self, prof, key, stack, bb):
        """Net EV of an SB open-jam: they fold (we win the big blind) or
        call with their jam-calling range."""
        w = 100.0 if (prof["random"] or prof["maniac"]) else 100.0 * prof["jam_call_w"]
        eq = self._eq_top(key, w)
        f = prof["f_jam"]
        return f * bb + (1 - f) * (eq * 2 * stack - stack)

    def _preflop(self, gs, opp, prof, key):
        me = self.player_id
        c = self.cur
        eqr = self.vs_random[key]
        pct = self.pct[key]
        to_call = gs.call_amount
        po = gs.pot_odds
        bb = gs.big_blind
        opp_allin = gs.status(opp) == "all_in"
        raised = gs.current_bet > bb

        # --- facing an all-in: pure pot-odds call vs their jamming range
        if opp_allin and to_call > 0:
            x = self._jam_range_pct(prof, over_raise=c["we_raised_pre"])
            eq = self._eq_top(key, x)
            if eq > po + 0.005:
                return self._call()
            return PlayerAction.fold(me)

        # --- facing a raise (not all-in)
        if raised and to_call > 0:
            x_open = max(8.0, min(80.0, 100.0 * (prof["pfr"] if c["pre_raises"] == 1 else prof["pfr"] * 0.5)))
            if prof["random"]:
                x_open = 100.0
            eq = self._eq_top(key, x_open)
            # value jam
            if eq >= 0.58 or pct <= 0.06:
                return self._raise(gs, gs.max_raise_to)
            # light 3-bet jam when they fold to 3-bets a lot
            if c["pre_raises"] == 1 and gs.can_raise and not prof["station_pre"]:
                # Jam vs call vs fold, by EV from now (folding = 0).
                x_me = min(gs.stacks[me], gs.stacks[opp] + gs.bets[opp] - gs.bets[me])
                x_opp = x_me + gs.bets[me] - gs.bets[opp]
                call_w = 100.0 if (prof["random"] or prof["maniac"]) else min(x_open, 100.0 * prof["jam_call_w"])
                eq_jc = self._eq_top(key, call_w)
                f_j = prof["f_jam"]
                ev_jam = f_j * gs.pot + (1 - f_j) * (eq_jc * (gs.pot + x_me + x_opp) - x_me)
                ev_call = (eq - 0.03) * (gs.pot + to_call) - to_call
                if ev_jam > 0 and ev_jam > ev_call:
                    return self._raise(gs, gs.max_raise_to)
            if prof["station_pre"] and eq >= 0.52:
                return self._raise(gs, 3 * gs.current_bet)
            margin = 0.0 if prof["maniac"] or prof["aggro"] else 0.03
            if eq >= po + margin:
                return self._call()
            return PlayerAction.fold(me)

        # --- SB, first to act (to_call == small blind)
        if to_call > 0:
            if prof["maniac"] or prof["jam_over"] >= 0.15:
                # EV of opening vs a bot that folds / jams / calls our raise.
                stack = gs.stacks[me] + gs.bets[me]
                p_jam = min(0.95, prof["jam_over"])
                p_fold = min(1.0 - p_jam, prof["f_small_raise"])
                p_call = max(0.0, 1.0 - p_jam - p_fold)
                eq_j = self._eq_top(key, self._jam_range_pct(prof, over_raise=True))
                eq_c = self._eq_top(key, max(20.0, min(100.0, 100.0 * prof["vpip"])))
                best, best_size = -gs.small_blind, None
                for size in (2 * bb, 2.5 * bb, 3 * bb):
                    ev_call_jam = eq_j * 2 * stack - stack
                    ev = (p_fold * bb + p_jam * max(ev_call_jam, -size)
                          + p_call * ((2 * eq_c - 1) * size + 0.1 * size))
                    if ev > best:
                        best, best_size = ev, size
                ev_jam = self._ev_jam_preflop(prof, key, stack, bb)
                if ev_jam > best:
                    best, best_size = ev_jam, gs.max_raise_to
                if best_size is None:
                    return PlayerAction.fold(me)
                return self._raise(gs, best_size)
            if prof["folder"] or prof["f_pre"] >= 0.68:
                return self._raise(gs, 2 * bb)  # they fold: min-raise anything
            if prof["station_pre"]:
                if pct <= 0.18:
                    return self._raise(gs, 4 * bb)
                if pct <= 0.55:
                    return self._raise(gs, 2.5 * bb)
                if pct <= 0.72:
                    return self._call()  # limp, see a cheap flop in position
                return PlayerAction.fold(me)
            if pct <= 0.12:
                return self._raise(gs, 3 * bb)
            if pct <= 0.78:
                return self._raise(gs, 2.5 * bb)
            if pct <= 0.88 and prof["f_pre"] >= 0.5:
                return self._raise(gs, 2 * bb)
            return PlayerAction.fold(me)

        # --- BB facing a limp (free check available)
        if prof["folder"] or prof["f_pre"] >= 0.65:
            return self._raise(gs, 3 * bb)
        if pct <= 0.30:
            return self._raise(gs, 4 * bb if prof["station_pre"] else 3.5 * bb)
        return self._call()

    # ------------------------------------------------------------- postflop
    def _villain_pct(self, gs, opp, prof):
        c = self.cur
        s = self._stats(gs, opp)
        if opp in c["allin"]:
            return self._jam_range_pct(prof, over_raise=c["we_raised_pre"])
        if prof["random"]:
            return 100.0
        # Preflop range only: postflop narrowing is per street (_street_bands).
        if c["last_aggr"].get("preflop") == opp:
            return max(8.0, min(70.0, 100.0 * prof["pfr"]))
        # they called preflop: wide, capped range
        return max(8.0, min(100.0, 100.0 * prof["vpip"]))

    def _showdown_hist(self, gs, opp, prof):
        """10-bin weights over hand percentile from the hands they have
        shown after the same preflop action, or None below 8 showdowns."""
        c = self.cur
        if prof["random"] or prof["maniac"] or opp in c["allin"]:
            return None
        s = self._stats(gs, opp)
        shown = s["sd_pct_raised"] if c["opp_pre_raised"] else s["sd_pct_called"]
        if len(shown) < 8:
            return None
        bins = [0.5] * 10
        for x in shown:
            bins[min(9, int(x * 10))] += 1.0
        return bins

    def _street_bands(self, board, prof, honesty):
        """One strength band per street, from what they did on that street:
        a bet or raise keeps their stronger hands, a call drops their
        weakest (and their best, which would have raised). Each band is
        widened toward their whole range by how dishonest their shown
        aggression has been."""
        c = self.cur
        if prof["station"] or prof["maniac"] or prof["random"]:
            return []
        bands = []
        for street, n in (("flop", 3), ("turn", 4), ("river", 5)):
            if len(board) < n:
                break
            kinds = c["opp_line"][street]
            if "raise" in kinds:
                band = ((1.0 - max(0.1, prof["raise_bet"])) * honesty, 1.0)
            elif "bet" in kinds:
                band = ((1.0 - max(0.15, prof["bet_rate"])) * honesty, 1.0)
            elif "call" in kinds:
                band = (min(0.6, prof["f_post"]) * honesty,
                        1.0 - min(0.5, prof["raise_bet"]) * honesty)
            else:
                continue
            bands.append((n, band))
        return bands

    def _ranking(self, board, n, pool):
        """Strength of each pool combo on the first n board cards, cached
        for the hand: {combo key: evaluate() rank}. Only combos actually
        in a pool are evaluated (the engine's evaluate is slow)."""
        cache = self.cur["rank_cache"].setdefault(n, {})
        sub = list(board[:n])
        for cb in pool:
            k = str(cb[0]) + str(cb[1])
            if k not in cache:
                cache[k] = evaluate([cb[0], cb[1]] + sub)
        return cache

    def _postflop(self, gs, opp, prof, key):
        me = self.player_id
        c = self.cur
        board = list(gs.board)
        to_call = gs.call_amount
        pot = gs.pot
        po = gs.pot_odds
        x = self._villain_pct(gs, opp, prof)
        samples = self.mc_samples
        s = self._stats(gs, opp)
        # How often their showdown-revealed aggression was weak: a random or
        # bluff-heavy bettor's bets say little about their hand.
        weak = _rate(s["sd_aggr_weak"], s["sd_aggr"], 0.35, 3)
        honesty = max(0.0, 1.0 - weak)
        bands = self._street_bands(board, prof, honesty)
        hist = self._showdown_hist(gs, opp, prof)
        eq = self._mc_equity(board, x, samples, bands, hist)
        eff = gs.effective_stack(me)
        spr = eff / pot if pot else 9.0
        street = gs.street
        i_was_aggr = c["last_aggr"].get("preflop") == me or c["aggressor"] == me
        station, folder, maniac = prof["station"], prof["folder"], prof["maniac"]

        if to_call == 0:
            # Value: bet big, bigger vs stations; jam when committed.
            if eq >= 0.80 and spr <= 3.0:
                return self._raise(gs, gs.max_raise_to)
            value_size = pot * (1.0 if station else 0.75)
            if prof["size_sensitive"]:
                value_size = pot * 0.3  # they call small bets, fold to big
            if eq >= 0.66:
                if spr <= 1.3 and not prof["size_sensitive"]:
                    return self._raise(gs, gs.max_raise_to)
                return self._raise(gs, gs.bets[me] + value_size)
            if eq >= 0.54 and not station:
                size = pot * (0.45 if prof["f_post"] >= 0.5 else 0.6)
                if prof["size_sensitive"]:
                    size = pot * 0.3
                return self._raise(gs, gs.bets[me] + size)
            if eq >= 0.58 and station:
                return self._raise(gs, gs.bets[me] + pot * 0.75)
            # Bluffs only vs opponents who fold to bets.
            if folder or prof["f_post"] >= 0.62 or (prof["size_sensitive"] and prof["f_big"] >= 0.6):
                if self.rng.random() < 0.85:
                    bluff = pot * (0.6 if prof["size_sensitive"] else 0.4)
                    return self._raise(gs, gs.bets[me] + bluff)
            elif i_was_aggr and street == "flop" and prof["f_post"] >= 0.45 and eq >= 0.35:
                if self.rng.random() < 0.6:
                    return self._raise(gs, gs.bets[me] + pot * 0.45)
            return self._call()

        # Facing a bet.
        if maniac or prof["aggro"] or prof["random"]:
            margin = -0.01
        elif prof["passive"]:
            margin = 0.10
        else:
            margin = 0.04
        if street == "river":
            margin += 0.02
        if gs.status(opp) == "all_in":
            margin = 0.0 if not prof["passive"] else 0.06
            if eq > po + margin:
                return self._call()
            return PlayerAction.fold(me)
        if eq >= 0.74 and gs.can_raise:
            if spr <= 2.5 or eq >= 0.85:
                return self._raise(gs, gs.max_raise_to)
            return self._raise(gs, gs.current_bet * 2.8)
        if eq >= 0.62 and gs.can_raise and station:
            return self._raise(gs, gs.current_bet * 2.5)
        if eq >= po + margin:
            return self._call()
        return PlayerAction.fold(me)

    # ------------------------------------------------------------- multiway
    def _multiway(self, gs):
        me = self.player_id
        opps = len(gs.players_in_hand) - 1
        if gs.street == "preflop":
            key = hand_key(self.hand)
            eq = self.vs_random[key] ** max(1, opps)
        else:
            eq = self._mc_equity(list(gs.board), 100.0, 200) ** max(1, opps)
        if eq > 1.6 / (opps + 1) and gs.can_raise:
            return self._raise(gs, gs.pot)
        if eq > gs.pot_odds:
            return self._call()
        return self._fold_or_check(gs)

    # ------------------------------------------------------------ end_game
    def end_game(self, actions, gs):
        try:
            self._ingest(actions, gs)
            me = self.player_id
            for p in range(gs.num_players):
                if p == me:
                    continue
                s = self._stats(gs, p)
                s["hands"] += 1
                shown = self.cur["shown"].get(p)
                if shown and len(shown) == 2 and len(gs.board) == 5:
                    s["showdowns"] += 1
                    c = self.cur
                    key_ = "sd_pct_raised" if c["opp_pre_raised"] else "sd_pct_called"
                    s[key_].append(self.pct[hand_key(list(shown))])
                    if len(s[key_]) > 200:
                        del s[key_][0]
                    if c["opp_called_jam"]:
                        s["jamcall_n"] += 1
                        s["jamcall_pct_sum"] += self.pct[hand_key(list(shown))]
                    aggr_streets = [st for st, who in c["last_aggr"].items() if who == p and st != "preflop"]
                    if aggr_streets:
                        s["sd_aggr"] += 1
                        rank = evaluate(list(shown) + list(gs.board))
                        if rank[0] <= 1:  # high card or one pair when betting
                            s["sd_aggr_weak"] += 1
        except Exception:
            pass

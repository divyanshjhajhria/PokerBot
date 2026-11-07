"""Heads-up strategy for 25 bb deep duplicate play (Steps 4-7).

Scoring is bb/100 with stacks reset every hand, so chips are all that count:
every decision takes the option with the best chip EV measured from now
(chips already in the pot are sunk: folding is worth 0, taking the pot now
is worth `pot`). The opponent's behaviour comes from their Profile.

  preflop   EV of fold / open 2.5 bb / shove first-in, check / raise / shove
            over a limp, fold / call / 3-bet shove facing a raise. Equity
            is the heads-up class-vs-class matrix against the range their
            action implies: O(169), no Monte Carlo. At <= 10 bb with little
            data, the Nash push/call tables decide.
  postflop  their range is narrowed street by street by what they did
            (a bet keeps their strongest hands, a call drops their weakest),
            then Monte Carlo equity drives call / raise / bet / check. No
            pure bluffs against stations, maniacs or random players.
  all-ins   a jam is called when equity against the jammer's range beats
            the price plus a small risk premium; a maniac's range is every
            hand (Step 7).

Multiway pots only appear in the organisers' validity tests; they get plain
equity-vs-random play.
"""

import bot
from bot import (CALL, CHECK, CLASS_NCOMBOS, FOLD, RAISE, SMALL_BET, TWO_PAIR, Bot,
                 evaluate, equity_mc, parse_cards, preflop_equity,
                 preflop_equity_vs_range, range_combos, top_range)

STRATEGY_CONFIG = {
    'risk_premium': 0.02,    # chip-EV scoring: only a margin for model error
    'open_sizes': (2.0, 2.5, 3.0),   # SB open sizes (bb); EV picks one
    'iso_sizes': (3.0, 3.5, 4.0),    # BB raise over a limp (bb)
    'realize_ip': 0.95,      # share of equity realised postflop in position
    'realize_oop': 0.80,     # ... out of position
    'pushfold_bb': 10.0,     # at or below: shove or fold only
    'nash_min_hands': 10,    # fewer hands seen: trust the Nash tables
    'value_raise_eq': 0.72,  # postflop raise for value at this equity
    'call_margin': 0.02,
    'raise_discount': 0.03,  # extra margin when raised (not vs maniacs)
    'post_iters': 8000,      # Monte Carlo budget per postflop estimate
    'small_bet_frac': 0.33,  # the small candidate bet (a share of the pot)
}

ALL = [1.0] * 169
LOOSE = ('maniac', 'random')            # any two cards; actions say little
NO_BLUFF = ('station', 'maniac', 'random')


def band(lo, hi):
    """Classes ranked between the top `lo` and top `hi` share of hands."""
    hi_w, lo_w = top_range(min(1.0, hi)), top_range(max(0.0, lo))
    return [max(0.0, a - b) for a, b in zip(hi_w, lo_w)]


def share(weights):
    return sum(w * n for w, n in zip(weights, CLASS_NCOMBOS)) / 1326.0


def eq_vs(hole, weights):
    return preflop_equity_vs_range(hole, weights)


class Opp(object):
    """The opponent as the strategy sees them: profile rates plus labels."""

    def __init__(self, profile):
        self.p = profile
        self.labels = profile.labels()
        self.loose = any(x in LOOSE for x in self.labels)
        self.station = 'station' in self.labels
        self.hands = profile.c['hands']
        self.jammer = ('maniac' in self.labels or 'shove_over_raiser' in self.labels
                       or profile.rate('shove_over_raise') >= 0.10)

    def vs_raise(self):
        """Response to our raise: (fold, call, jam) shares and the call and
        jam ranges. At 25 bb every re-raise is treated as a jam."""
        f, j = self.p.rate('fold_vs_raise'), self.p.rate('three_bet')
        if f + j > 1.0:
            f, j = f / (f + j), j / (f + j)
        c = 1.0 - f - j
        jam = ALL if self.loose else top_range(j)
        call = ALL if (self.loose or self.station) else band(j, j + c)
        return f, c, j, call, jam

    def call_shove_frac(self, depth_bb):
        """P(they call a shove): their record, with HU Nash as the prior."""
        c = self.p.rate('call_vs_shove', prior=bot.nash_call_frac(min(20.0, depth_bb)))
        if 'maniac' in self.labels or self.station:
            c = max(c, 0.9)
        return c

    def first_in_range(self, kind):
        """Their range after acting first preflop."""
        if self.loose:
            return ALL
        if kind == 'shove':
            return top_range(max(0.02, self.p.rate('open_shove')))
        if kind == 'raise':
            return top_range(max(0.02, self.p.rate('open_raise')))
        r = self.p.rate('open_raise')
        return band(r, r + max(0.05, self.p.rate('open_limp')))

    def reraise_range(self, shove):
        if self.loose:
            return ALL
        return top_range(max(0.02, self.p.rate('shove_over_raise' if shove else 'three_bet')))


class Ctx(object):
    """One decision's numbers, all in chips."""

    def __init__(self, state, L):
        self.hole = parse_cards(state['hole'])
        self.board = parse_cards(state.get('board'))
        self.pot = int(state['pot'])
        self.bb = L.bb
        self.to_call = L.to_call
        self.my_bet = L.my_bet
        self.stack = L.stack
        self.L = L
        self.opp_bet = int(state['opp_bet'])
        self.opp_stack = int(state['opp_stack'])
        self.ip = bool(state.get('is_button'))   # HU: the button acts last postflop
        self.line = state['line']
        # Chips each side can still add if everything goes in from here.
        self.x_me = max(0, min(self.stack, self.opp_stack + self.opp_bet - self.my_bet))
        self.x_opp = max(0, self.x_me + self.my_bet - self.opp_bet)
        start = state.get('start_eff')
        self.depth_bb = (start if start else min(self.stack + self.my_bet,
                                                  self.opp_stack + self.opp_bet)) / float(self.bb)

    def kinds(self, who, street):
        return [k for s, w, k in self.line if w == who and s == street]

    def allin_ev(self, eq):
        """EV of all chips going in now and the hand running out."""
        return eq * (self.pot + self.x_me + self.x_opp) - self.x_me

    def call_ev(self, eq, realize):
        """EV of calling; exact when the call ends the betting (all-in)."""
        excess = max(0, self.opp_bet - (self.my_bet + self.to_call))
        contested = self.pot + self.to_call - excess
        if self.opp_stack == 0 or self.to_call >= self.stack:
            realize = 1.0
        return realize * eq * contested - self.to_call


class PokerBot(Bot):
    """bot.Bot's safety shell with the real strategy plugged in."""

    def __init__(self, seed=0, config=None, tables=None):
        cfg = dict(STRATEGY_CONFIG)
        cfg.update(config or {})
        Bot.__init__(self, seed=seed, config=cfg, tables=tables)

    def _decide(self, state, L, deadline):
        opps = state.get('opponents') or []
        if len(opps) != 1 or state.get('line') is None or 'opp_bet' not in state:
            return self._multiway(state, L, deadline)
        c = Ctx(state, L)
        opp = Opp(self.profile(opps[0]))
        if not c.board:
            return self._preflop(c, opp)
        return self._postflop(c, opp, deadline)

    # -- preflop ---------------------------------------------------------

    def _preflop(self, c, opp):
        cfg = self.cfg
        mine, theirs = c.kinds('me', 'preflop'), c.kinds('opp', 'preflop')
        pushfold = c.depth_bb <= cfg['pushfold_bb']
        thin = opp.hands < cfg['nash_min_hands']
        opp_allin = c.opp_stack == 0

        if not mine and not theirs:                  # SB, first in
            if c.to_call == 0:
                return (CHECK, 0)
            if pushfold and thin:
                return (RAISE, c.L.max_raise_to) if bot.push_ok(c.hole, c.depth_bb) else (FOLD, 0)
            opts = [((FOLD, 0), 0.0), ((RAISE, c.L.max_raise_to), self._ev_shove(c, opp, ALL))]
            if not pushfold:
                # Our response model does not depend on raise size, so only
                # pick a size against jammers, where risking less matters.
                sizes = cfg['open_sizes'] if opp.jammer else (2.5,)
                for size in sizes:
                    opts.append(((RAISE, size * c.bb), self._ev_open(c, opp, size)))
            return self._best(opts)

        if not mine and theirs == ['call'] and c.to_call == 0:   # BB vs a limp
            limp = opp.first_in_range('call')
            opts = [((CHECK, 0), cfg['realize_oop'] * eq_vs(c.hole, limp) * c.pot),
                    ((RAISE, c.L.max_raise_to), self._ev_shove(c, opp, limp))]
            if not pushfold:
                sizes = cfg['iso_sizes'] if opp.jammer else (4.0,)
                for size in sizes:
                    opts.append(((RAISE, size * c.bb),
                                 self._ev_open(c, opp, size, their=limp)))
            return self._best(opts)

        if c.to_call == 0:
            return (CHECK, 0)

        last = theirs[-1] if theirs else 'raise'
        shove = last == 'shove' or opp_allin
        if not mine and len(theirs) == 1:            # BB vs an open or open shove
            if shove and pushfold and thin:
                return (CALL, c.to_call) if bot.call_ok(c.hole, c.depth_bb) else (FOLD, 0)
            rng = opp.first_in_range('shove' if shove else 'raise')
        else:                                        # facing a re-raise
            rng = opp.reraise_range(shove)
        return self._facing(c, opp, rng, c.ip)

    def _ev_shove(self, c, opp, their):
        """EV of moving all-in; they call with the top of `their` range."""
        if c.x_opp <= 0:   # they cannot call more: same as calling
            return c.call_ev(eq_vs(c.hole, their), 1.0)
        call = opp.call_shove_frac(c.depth_bb)
        if opp.loose or opp.station:
            call_rng, p_call = their, call
        else:
            call_rng = [min(a, b) for a, b in zip(their, top_range(call))]
            p_call = share(call_rng) / max(1e-9, share(their))
        if p_call <= 0:
            return float(c.pot)
        return (1 - p_call) * c.pot + p_call * c.allin_ev(eq_vs(c.hole, call_rng))

    def _ev_open(self, c, opp, size_bb, their=None):
        """EV of raising to size_bb: they fold, call, or jam over it."""
        cfg = self.cfg
        to = size_bb * c.bb
        added = to - c.my_bet
        f, p_call, p_jam, call_rng, jam_rng = opp.vs_raise()
        if their is not None:   # their range is already capped (e.g. a limp)
            call_rng = [min(a, b) for a, b in zip(call_rng, their)]
            jam_rng = [min(a, b) for a, b in zip(jam_rng, their)] if not opp.loose else their
        realize = cfg['realize_ip'] if c.ip else cfg['realize_oop']
        called_pot = c.pot + added + (to - c.opp_bet)
        ev_called = realize * eq_vs(c.hole, call_rng) * called_pot - added
        ev_jammed = max(-added, c.allin_ev(eq_vs(c.hole, jam_rng)))
        return f * c.pot + p_call * ev_called + p_jam * ev_jammed

    def _facing(self, c, opp, their, ip):
        """Fold / call / jam against a raise from range `their`."""
        cfg = self.cfg
        eq = eq_vs(c.hole, their)
        realize = cfg['realize_ip'] if ip else cfg['realize_oop']
        opts = [((FOLD, 0), 0.0),
                ((CALL, c.to_call), c.call_ev(eq - cfg['risk_premium'], realize))]
        if c.L.can_raise and c.opp_stack > 0:
            opts.append(((RAISE, c.L.max_raise_to), self._ev_shove(c, opp, their)))
        return self._best(opts)

    @staticmethod
    def _best(opts):
        return max(opts, key=lambda o: o[1])[0]

    # -- postflop --------------------------------------------------------

    def _opp_range(self, c, opp):
        """Their combos now: preflop range, narrowed by each street's line."""
        theirs, mine = c.kinds('opp', 'preflop'), c.kinds('me', 'preflop')
        if opp.loose:
            w = ALL
        elif 'shove' in theirs:
            w = opp.reraise_range(True) if mine else opp.first_in_range('shove')
        elif 'raise' in theirs:
            w = opp.reraise_range(False) if mine and mine[0] in ('raise', 'shove') \
                else opp.first_in_range('raise')
        elif 'call' in theirs and mine and 'raise' in mine:
            w = opp.vs_raise()[3]
        elif 'call' in theirs:
            w = opp.first_in_range('call')
        else:
            w = ALL
        combos = range_combos(w, dead=c.hole + c.board)
        if opp.loose or not combos:
            return combos or None
        # Loose players bet wider than their value hands: read bets less.
        agg_keep = max(0.25, min(1.0, opp.p.agg_freq() * (1.0 + opp.p.rate('vpip'))))
        call_keep = 1.0 if opp.station else max(0.3, 1.0 - opp.p.rate('fold_to_bet'))
        for street, n in (('flop', 3), ('turn', 4), ('river', 5)):
            if len(c.board) < n:
                break
            k = c.kinds('opp', street)
            if 'raise' in k or 'shove' in k:
                combos = strongest(combos, c.board[:n], agg_keep)
            elif 'call' in k:
                combos = strongest(combos, c.board[:n], call_keep)
        return combos

    def _postflop(self, c, opp, deadline):
        cfg = self.cfg
        combos = self._opp_range(c, opp)
        iters = cfg['post_iters']
        eq, _ = equity_mc(c.hole, c.board, 1, ranges=[combos] if combos else None,
                          iters=iters, deadline=deadline, rng=self.rng)
        street = {3: 'flop', 4: 'turn', 5: 'river'}[len(c.board)]

        if c.to_call > 0:
            need = c.to_call / float(c.pot + c.to_call)
            raised = bool(c.kinds('me', street)) and not opp.loose
            margin = cfg['call_margin'] + (cfg['raise_discount'] if raised else 0.0)
            if (c.L.can_raise and c.opp_stack > 0 and eq >= cfg['value_raise_eq']):
                pot_after = c.pot + c.to_call
                if c.x_me <= 1.5 * pot_after or eq >= 0.85:
                    return (RAISE, c.L.max_raise_to)
                return (RAISE, c.my_bet + c.to_call + pot_after)
            if eq >= need + margin:
                return (CALL, c.to_call)
            return (FOLD, 0)

        if not c.L.can_raise or c.x_me <= 0:
            return (CHECK, 0)
        # Bet or check. Each candidate size has its own fold rate (they may
        # call small bets and fold to big ones) and so its own calling range.
        pot = float(c.pot)
        spr = c.x_me / pot
        sizes = [cfg['small_bet_frac'] * pot, (0.75 if wet(c.board) else 0.60) * pot, pot]
        if spr <= 3.0:
            sizes.append(float(c.x_me))
        no_bluff = any(x in NO_BLUFF for x in opp.labels)
        eq_cache = {}
        best_ev, best_b = None, None
        for b in sizes:
            b = min(float(c.x_me), max(float(c.bb), b))
            f = opp.p.rate('fold_small' if b <= SMALL_BET * pot else 'fold_big')
            if opp.station:
                f = min(f, 0.15)
            elif opp.loose:
                f = min(f, 0.30)
            key = round(f, 3)
            if key not in eq_cache:
                if combos is None or opp.loose or f < 0.05:
                    eq_cache[key] = eq
                else:
                    callers = strongest(combos, c.board, 1.0 - f)
                    eq_cache[key] = equity_mc(c.hole, c.board, 1, ranges=[callers],
                                              iters=iters // 2, deadline=deadline,
                                              rng=self.rng)[0]
            eq_c = eq_cache[key]
            if no_bluff and eq_c < 0.5:
                continue
            ev = f * pot + (1 - f) * (eq_c * (pot + 2 * b) - b)
            if best_ev is None or ev > best_ev:
                best_ev, best_b = ev, b
        ev_check = eq * pot * (cfg['realize_ip'] if c.ip else cfg['realize_oop'])
        if best_b is not None and best_ev > ev_check:
            return (RAISE, c.my_bet + best_b)
        return (CHECK, 0)

    # -- multiway fallback -------------------------------------------------

    def _multiway(self, state, L, deadline):
        hole = parse_cards(state['hole'])
        board = parse_cards(state.get('board'))
        n = max(1, int(state.get('num_opponents', 1) or 1))
        pot = max(0, int(state.get('pot', 0) or 0))
        if not board and n == 1:
            eq = preflop_equity(hole, 1)
        else:
            eq, _ = equity_mc(hole, board, n, iters=2000, deadline=deadline, rng=self.rng)
        fair = 1.0 / (n + 1)
        if L.to_call == 0:
            if eq > 1.6 * fair and L.can_raise:
                return (RAISE, L.my_bet + max(2.5 * L.bb, 0.66 * pot))
            return (CHECK, 0)
        need = L.to_call / float(pot + L.to_call)
        if eq > max(0.6, 2.2 * fair) and L.can_raise:
            return (RAISE, L.max_raise_to)
        if eq > need + 0.04:
            return (CALL, L.to_call)
        return (FOLD, 0)


# -- board helpers ---------------------------------------------------------

def strongest(combos, board, keep):
    """The top `keep` share of combos by made-hand strength on `board`."""
    if keep >= 0.999 or len(combos) <= 3:
        return combos
    scored = sorted(combos, key=lambda ab: -evaluate([ab[0], ab[1]] + board))
    return scored[:max(3, int(round(keep * len(scored))))]


def wet(board):
    """A flush or straight is possible or one card away."""
    suits = [0, 0, 0, 0]
    for x in board:
        suits[x & 3] += 1
    if max(suits) >= 2 and len(board) <= 4 or max(suits) >= 3:
        return True
    ranks = sorted(set(x >> 2 for x in board))
    for i in range(len(ranks)):
        if sum(1 for r in ranks if ranks[i] <= r <= ranks[i] + 4) >= 3:
            return True
    return False


def made_two_pair(hole, board):
    """Two pair or better that uses at least one hole card."""
    v = evaluate(hole + board)
    if v >> 20 < TWO_PAIR:
        return False
    return len(board) < 5 or v != evaluate(board)

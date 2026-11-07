"""Reads hands from the actions we are shown (Steps 3 and 6).

run() and end_game() each receive the actions since our last turn; joined
in order they are the whole hand. We replay them through the engine's public
GameState (each player is meant to keep its own copy), so every action is
seen in context: the street, what it cost to call, whether it was all-in,
whether it faced a raise. Two readers use the replay:

  hand_events  after the hand: per-opponent counter increments for Profile
  hand_line    during the hand: who did what on each street, for ranges

Nothing here is kept between hands, so the model's pickle stays plain data.
"""

from bot import HIGH, PAIR, SMALL_BET, evaluate, parse_card
from game_state import GameState
from player_action import DEALER

SHOVE_FRAC = 0.85        # a raise to >= 85% of everything counts as a shove
UNKNOWN_STACK = 10 ** 7  # when we never saw the start (all-in detection off)
OVERBET = 2.0            # raise increment above this many pots, not all-in


def start_stacks(game_state):
    """Chips each seat started the hand with, read on our first preflop
    turn (stacks behind plus this street's bets, blinds included)."""
    return [s + b for s, b in zip(game_state.stacks, game_state.bets)]


def replay(actions, stacks, button, small_blind, big_blind, teams):
    """(steps, shown, board): one step per betting action or dealt card,
    with the context just before it; shown = {seat: hole cards revealed}."""
    n = len(teams)
    stacks = list(stacks) if stacks else [UNKNOWN_STACK] * n
    st = GameState(stacks, teams=list(teams))
    st.setup_hand(stacks, button, small_blind, big_blind, list(teams))
    steps, shown = [], {}
    for a in actions:
        if a.player_id == DEALER:
            st.process_action(a)
            steps.append({'deal': True, 'street': st.street, 'board': list(st.board),
                          'in_hand': list(st.players_in_hand)})
            continue
        if a.kind == 'reveal_card':
            shown.setdefault(a.player_id, []).append(a.card)
            continue
        if a.kind == 'win':
            continue
        p = a.player_id
        to_call = st.call_amount
        allin_to = st.bets[p] + st.stacks[p]
        is_raise = a.kind == 'raise'
        is_shove = is_raise and a.amount >= SHOVE_FRAC * allin_to
        others_allin = any(st.stacks[q] == 0 for q in st.players_in_hand if q != p)
        steps.append({
            'deal': False, 'seat': p, 'kind': a.kind, 'street': st.street,
            'to_call': to_call, 'is_raise': is_raise, 'is_shove': is_shove,
            'facing_raise': (st.street == 'preflop' and to_call > 0
                             and st.current_bet > st.big_blind),
            'facing_allin': to_call > 0 and (to_call >= st.stacks[p] or others_allin),
            'small_bet': to_call > 0 and to_call <= SMALL_BET * max(1, st.pot - to_call),
            'overbet': (is_raise and not is_shove
                        and a.amount - st.current_bet > OVERBET * st.pot),
        })
        st.process_action(a)
    return steps, shown, list(st.board)


def kind_of(step):
    """'fold', 'check', 'call', 'raise' or 'shove'."""
    if step['is_shove']:
        return 'shove'
    if step['is_raise']:
        return 'raise'
    if step['kind'] == 'call':
        return 'call' if step['to_call'] > 0 else 'check'
    return step['kind']


def hand_line(actions, me, stacks, button, small_blind, big_blind, teams):
    """[(street, 'me' or 'opp', kind)] for the hand so far."""
    steps, _, _ = replay(actions, stacks, button, small_blind, big_blind, teams)
    return [(s['street'], 'me' if s['seat'] == me else 'opp', kind_of(s))
            for s in steps if not s['deal']]


def weak_showdown(hole, board):
    """True when the hand shown has nothing of its own: high card, a
    pair that is only on the board, or the board playing."""
    h = [parse_card(str(c)) for c in hole]
    b = [parse_card(str(c)) for c in board]
    if len(h) != 2 or len(b) < 3:
        return False
    v = evaluate(h + b)
    cat = v >> 20
    if cat == HIGH:
        return True
    if cat == PAIR:
        return ((v >> 16) & 15) not in (h[0] >> 2, h[1] >> 2)
    return len(b) == 5 and v == evaluate(b)


def hand_events(actions, me, stacks, button, small_blind, big_blind, teams):
    """{seat: {counter: increment}} for every seat except `me`."""
    steps, shown, board = replay(actions, stacks, button, small_blind,
                                 big_blind, teams)
    ev = dict((p, {}) for p in range(len(teams)) if p != me)

    def inc(p, key):
        if p in ev:
            ev[p][key] = ev[p].get(key, 0) + 1

    acted_pre, vpip, pfr, shoved = set(), set(), set(), set()
    pot_entered = False   # someone called or raised preflop
    pre_raiser = None     # last preflop raiser
    flop_bettor = None    # first bet on the flop
    flop_raises = 0
    last_raiser = None    # most recent raise in the hand

    for s in steps:
        if s['deal']:
            if len(s['board']) == 3 and s['street'] == 'flop':
                for p in s['in_hand']:
                    inc(p, 'saw_flop')
            continue
        p, kind, to_call = s['seat'], s['kind'], s['to_call']
        is_raise, is_shove = s['is_raise'], s['is_shove']
        if s['street'] == 'preflop':
            if p not in acted_pre:
                acted_pre.add(p)
                inc(p, 'pre_opp')
                if not pot_entered:
                    inc(p, 'first_in')
                    if is_raise:
                        inc(p, 'open_raise')
                        if is_shove:
                            inc(p, 'open_shove')
                    elif kind == 'call' and to_call > 0:
                        inc(p, 'open_limp')
                    elif kind == 'fold':
                        inc(p, 'open_fold')
            if s['facing_raise']:
                inc(p, 'face_raise')
                if kind == 'fold':
                    inc(p, 'fold_vs_raise')
                elif is_raise:
                    inc(p, 'three_bet')
                    if is_shove:
                        inc(p, 'shove_over_raise')
            if s['facing_allin']:
                inc(p, 'face_shove')
                if kind == 'call':
                    inc(p, 'call_vs_shove')
            if is_raise or (kind == 'call' and to_call > 0):
                vpip.add(p)
                pot_entered = True
            if is_raise:
                pfr.add(p)
                pre_raiser = p
                if is_shove:
                    shoved.add(p)
        else:
            if to_call > 0:
                inc(p, 'face_bet')
                size = 'small' if s['small_bet'] else 'big'
                inc(p, 'face_' + size)
                if kind == 'fold':
                    inc(p, 'fold_to_bet')
                    inc(p, 'fold_' + size)
                if (s['street'] == 'flop' and flop_raises == 1
                        and flop_bettor == pre_raiser and flop_bettor != p):
                    inc(p, 'face_cbet')
                    if kind == 'fold':
                        inc(p, 'fold_to_cbet')
            if is_raise:
                inc(p, 'post_aggr')
                if s['street'] == 'flop':
                    flop_raises += 1
                    if flop_bettor is None:
                        flop_bettor = p
            elif kind == 'call':
                inc(p, 'post_calls' if to_call > 0 else 'post_checks')
        if is_raise:
            last_raiser = p
            inc(p, 'raises')
            if s['overbet']:
                inc(p, 'overbets')

    for p in vpip:
        inc(p, 'vpip')
    for p in pfr:
        inc(p, 'pfr')
    for p in shoved:
        inc(p, 'shoves')
    if len(shown) > 1:  # a real showdown
        for p, hole in shown.items():
            inc(p, 'showdown')
            if p == last_raiser:
                inc(p, 'sd_aggr')
                if weak_showdown(hole, board):
                    inc(p, 'sd_bluff')
    return ev

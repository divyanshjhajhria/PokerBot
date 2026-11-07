from hypothesis import given, strategies as st

from engine import play_hand, payouts
from player_action import PlayerAction as A
from poker_test_helpers import RandomBot, replay, std_deck


@st.composite
def hand_args(draw):
    n = draw(st.integers(2, 6))
    deck = list(draw(st.permutations(std_deck())))
    stacks = draw(st.lists(st.integers(0, 1000), min_size=n, max_size=n))
    small_blind = draw(st.integers(1, 50))
    big_blind = draw(st.integers(small_blind, 100))
    button = draw(st.integers(0, n - 1))
    teams = draw(st.one_of(st.none(), st.lists(
        st.text(alphabet="abcdef", min_size=1, max_size=3),
        min_size=n, max_size=n, unique=True)))
    weights = [0, 1, 1, 1, 2, 3, 1, 2, 3, 4, 4, 5, 6, 7, 8, 9]
    scripts = [draw(st.lists(st.sampled_from(weights), min_size=1,
                             max_size=12)) for _ in range(n)]
    seed_faults = [draw(st.sampled_from([False, False, False, False, True]))
                   for _ in range(n)]
    return dict(deck=deck, stacks=stacks, small_blind=small_blind,
                big_blind=big_blind, button=button, teams=teams,
                scripts=scripts, seed_faults=seed_faults)


def run(args):
    models = [RandomBot(s, f) for s, f in zip(args["scripts"],
                                               args["seed_faults"])]
    result = play_hand(models, args["deck"], args["stacks"],
                       button=args["button"],
                       small_blind=args["small_blind"],
                       big_blind=args["big_blind"], teams=args["teams"])
    return models, result


@given(hand_args())
def test_P05_pending_lists_match_history(args):
    """P-05: each seat's received actions lists, concatenated, are a prefix of HandResult.actions, each followed by that seat's move (own moves included in the next list)."""
    models, result = run(args)
    for seat, m in enumerate(models):
        if args["seed_faults"][seat]:
            assert m.runs == []
            continue
        pos = 0
        for rec in m.runs:
            got = rec["actions"]
            assert result.actions[pos:pos + len(got)] == got
            pos += len(got)
            # the move this run produced comes next (and opens the next list)
            assert result.actions[pos].player_id == seat


@given(hand_args())
def test_P06_replay_reproduces_net(args):
    """P-06: replaying HandResult.actions on a fresh GameState ends the hand (then only reveals and wins), ends with stacks == start + net, net equals payouts minus contributions, sums to 0 and never loses more than a stack."""
    _, result = run(args)
    stacks = args["stacks"]
    state, _, contributed = replay(result, stacks, args["button"],
                                   args["small_blind"], args["big_blind"],
                                   args["teams"])
    pay = payouts(contributed, list(state.folded), result.hands, result.board,
                  args["button"])
    assert result.net == [p - c for p, c in zip(pay, contributed)]
    assert sum(result.net) == 0
    assert all(net >= -stack for net, stack in zip(result.net, stacks))
    assert list(state.stacks) == [s + n for s, n in zip(stacks, result.net)]


@given(hand_args())
def test_P07_board_and_hands_from_deck(args):
    """P-07/H-01: hands[i] = deck[2i:2i+2]; board = deck[2n:...] = the dealer's reveal_card cards; 5 cards if >1 seat is unfolded at the end."""
    _, result = run(args)
    deck, n = args["deck"], len(args["stacks"])
    assert result.hands == [deck[2 * i:2 * i + 2] for i in range(n)]
    assert result.board == deck[2 * n:2 * n + len(result.board)]
    assert [a.card for a in result.actions
            if a.kind == "reveal_card" and a.player_id == -1] == result.board
    state, _, _ = replay(result, args["stacks"], args["button"],
                      args["small_blind"], args["big_blind"], args["teams"])
    if len(state.players_in_hand) > 1:
        assert len(result.board) == 5


@given(hand_args())
def test_P08_faults_and_replacements(args):
    """P-08: faults keys are exactly the faulting seats; a faulting turn becomes call if nothing owed, else fold; clean moves are kept."""
    models, result = run(args)
    _, before, _ = replay(result, args["stacks"], args["button"],
                       args["small_blind"], args["big_blind"], args["teams"])
    turn = [0] * len(models)
    for action, (to_act, owed) in zip(result.actions, before):
        if action.kind in ("reveal_card", "win"):
            continue
        seat = action.player_id
        assert seat == to_act
        replacement = A.call(seat) if owed == 0 else A.fold(seat)
        if args["seed_faults"][seat]:
            assert action == replacement
            continue
        rec = models[seat].runs[turn[seat]]
        turn[seat] += 1
        assert rec["call_amount"] == owed
        assert action == (replacement if rec["fault"] else rec["returned"])
    for seat, m in enumerate(models):
        if not args["seed_faults"][seat]:
            assert turn[seat] == len(m.runs)
    faulted = {s for s, m in enumerate(models)
               if args["seed_faults"][s] or any(r["fault"] for r in m.runs)}
    assert set(result.faults) == faulted
    assert all(isinstance(v, str) and v for v in result.faults.values())

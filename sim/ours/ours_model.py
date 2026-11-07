"""Our submission: the engine's Model API on top of strategy.PokerBot.

bot.py (evaluator, equity tables, safety shell, profiles), strategy.py (the
decisions) and tracker.py (reading hands) hold the poker. This file only
translates between the engine's GameStateView / PlayerAction and the bot's
plain state dict / action dict, and adds a last guard so that nothing here
can turn into a fault.
"""

import tracker
from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction
from strategy import PokerBot


class Model:
    def __init__(self):
        # PokerBot() loads the precomputed tables into plain attributes, so
        # they travel inside the pickle: nothing is read from disk at play time.
        self.core = PokerBot(seed=20261107)
        self.hand = []
        self.player_id = None
        self.hand_actions = []    # this hand so far, for the profiler
        self.start_stacks = None

    def seed(self, hand: list[Card], player_id: int):
        self.hand = hand
        self.player_id = player_id
        self.hand_actions = []
        self.start_stacks = None

    def run(
        self, actions: list[PlayerAction], game_state: GameStateView
    ) -> PlayerAction:
        me = self.player_id
        try:
            self.hand_actions.extend(actions)
            if self.start_stacks is None and game_state.street == 'preflop':
                self.start_stacks = tracker.start_stacks(game_state)
        except Exception as error:
            self.core._note_error(error)
        try:
            out = self.core.act(self._view_to_state(game_state))
            action = self._to_action(out)
            game_state.validate_action(action)
            return action
        except Exception as error:
            self.core._note_error(error)
            if game_state.call_amount == 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

    def end_game(
        self, actions: list[PlayerAction], game_state: GameStateView
    ):
        """Learn from the hand: one profile update per opponent seat."""
        try:
            self.hand_actions.extend(actions)
            teams = game_state.teams
            events = tracker.hand_events(
                self.hand_actions, self.player_id, self.start_stacks,
                game_state.button, game_state.small_blind,
                game_state.big_blind, teams)
            for seat, ev in events.items():
                self.core.observe(teams[seat], ev)
        except Exception as error:
            self.core._note_error(error)
        self.hand_actions = []   # keep the pickle plain between hands

    # -- translation ----------------------------------------------------

    def _view_to_state(self, gs: GameStateView) -> dict:
        me = self.player_id
        others = [p for p in gs.players_in_hand if p != me]
        state = {
            'hole': [str(card) for card in self.hand],
            'board': [str(card) for card in gs.board],
            'pot': gs.pot,
            'to_call': gs.call_amount,
            'stack': gs.stacks[me],
            'my_bet': gs.bets[me],
            'min_raise_to': gs.min_raise_to,
            'max_raise_to': gs.max_raise_to,
            'can_raise': gs.can_raise,
            'big_blind': gs.big_blind,
            'num_opponents': len(others),
            'opponents': [gs.teams[p] for p in others],
        }
        if len(others) == 1:   # heads-up: what the strategy needs
            opp = others[0]
            state['opp_bet'] = gs.bets[opp]
            state['opp_stack'] = gs.stacks[opp]
            state['is_button'] = gs.button == me
            if self.start_stacks is not None:
                state['start_eff'] = min(self.start_stacks[me], self.start_stacks[opp])
            try:
                state['line'] = tracker.hand_line(
                    self.hand_actions, me, self.start_stacks, gs.button,
                    gs.small_blind, gs.big_blind, gs.teams)
            except Exception as error:   # no line: the strategy plays safe
                self.core._note_error(error)
        return state

    def _to_action(self, out: dict) -> PlayerAction:
        me = self.player_id
        if out['action'] == 'raise':
            return PlayerAction.raise_to(me, int(out['amount']))
        if out['action'] in ('call', 'check'):
            return PlayerAction.call(me)  # call checks when nothing is owed
        return PlayerAction.fold(me)

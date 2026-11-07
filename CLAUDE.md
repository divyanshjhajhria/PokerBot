# Poker bot arena

No-limit Texas hold'em for bots. A team writes a `Model` in `model.py`; the
model object is pickled and submitted, and the organiser's engine plays it
against other teams' models.

This file has two sections. **Default to "For bot authors"**: almost everyone
working in this repo is writing a bot. Use "For maintainers" only when the
user says they maintain the engine.

## For bot authors

### Setup

Python 3.10+ and Pydantic, pinned in `requirements.txt` (the engine uses the
same version, so keep the pin). From the repo root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run everything with `.venv/bin/python` (in VS Code, select `.venv` as the
interpreter): the system `python3` cannot import the code without Pydantic.
On Ubuntu, if `venv` or `pip` is missing: `sudo apt install python3-venv
python3-pip`.

### Rules for Claude

- Edit only `model.py` and new files you add next to it. Never edit a file
  headed `DO NOT CHANGE THIS CODE` (`card_utils.py`, `player_action.py`,
  `game_state.py`, `game_interface.py`): the organiser's
  copies are what play, so local changes only make your tests lie.
- The model is pickled. Keep its attributes plain data (numbers, lists,
  dicts, numpy arrays, model weights). No lambdas, open files, sockets,
  threads or generators as attributes. Load weights and data in `__init__`.
- Imports are flat (`from card_utils import Card`): run code from the repo root,
  with `.venv/bin/python`.
- Never guess a `GameStateView` name: use only those listed below. Editors
  cannot autocomplete them, and any other name raises `AttributeError`.

### How a bot is run

1. `Model()` is constructed and pickled once.
2. Each hand, the engine builds `GameInterface(model, game_state, player_id,
   hand)`, which calls `model.seed(hand, player_id)` once. Store both:
   `run` is not given them again.
3. On each of your turns, `model.run(actions, game_state)` is called:
   - `actions`: every `PlayerAction` since your last turn this hand, oldest
     first, your own previous move and the dealer's cards included.
   - `game_state`: a read-only `GameStateView` of the hand, already updated
     with `actions`.
4. `run` returns one `PlayerAction` with your own `player_id`. A non-
   `PlayerAction`, someone else's id or an illegal move raises
   `InvalidAction`.
5. After every hand, `model.end_game(actions, game_state)` is called if
   your model has it (optional): `actions` is everything since your last
   turn, ending with the showdown cards and the pot awards (see below), and
   `game_state` is the final state. Learn from the hand here; there is
   nothing to clear, since `seed` overwrites the per-hand fields next hand.

The same `Model` object plays hand after hand (in evals, within one seat
rotation), so whatever it keeps in attributes between hands is what it has
learned, e.g. per opponent by `game_state.teams`.

### Errors

Two kinds, both subclasses of `ValueError`:

- `pydantic.ValidationError`: a value has the wrong type or range, e.g.
  `Card("1", "S")`, `PlayerAction.raise_to(0, 2.5)`, `evaluate` on 4 cards.
  Checks are strict: no coercion, so `"5"` and `True` are not numbers. For
  positional arguments the message names the argument by position from
  `0`, counting `self` in methods: `PlayerAction / 0 / Input should be a
  valid integer` means `player_id`, `GameState.set_stack / 2 / ...` means
  `chips`.
- `InvalidAction` (`game_state.py`): the move breaks the rules of the hand,
  e.g. out of turn or a raise outside `raise_range`.

The horizon is one hand: a fresh `GameInterface` (and `seed`) every hand.

### Game rules as implemented

- Players are numbered `0..n-1`; every per-player tuple is indexed by id.
  The dealer is player `-1` (`player_action.DEALER`).
- Blinds are posted automatically (default 10/20, stacks 500). Heads-up, the
  button posts the small blind and acts first preflop; after the flop the
  first player after the button acts first.
- The dealer turns board cards over one at a time (`kind == "reveal_card"`,
  `player_id == DEALER`): the 3rd, 4th and 5th card open the flop, turn and
  river. Check `player_id == DEALER` before treating a `reveal_card` as a
  board card: players' shown cards use the same kind.
- When the hand is over, at a showdown every player still in shows both
  hole cards (`reveal_card` with their own `player_id`), seats from the
  button's left. A player who wins because everyone else folded shows
  nothing. Then each player who won chips gets one `win` (`amount` = total
  chips from every pot, their own uncalled chips included).
- `raise_to(total)` names your **total bet this street**, not the increment.
  Legal totals are `min_raise_to..max_raise_to` (`raise_range`).
- A raise must be at least the last full raise (`last_raise`). An all-in
  smaller than that does not reopen betting for players who already acted.
- Fold and call are always legal on your turn. `call` checks when nothing is
  owed and calls all-in for less when your stack is short.

### API

`PlayerAction` (`player_action.py`), always built with a constructor:

| Constructor | Meaning |
|---|---|
| `PlayerAction.fold(player_id)` | fold |
| `PlayerAction.call(player_id)` | call, or check when `call_amount == 0` |
| `PlayerAction.raise_to(player_id, total)` | raise to a street total |

Fields: `player_id`, `kind` (`"fold"`, `"call"`, `"raise"`, `"reveal_card"`,
`"win"`), `amount` (raise total, or chips won), `card` (for `reveal_card`).
Only the engine builds `reveal_card` and `win`; returning them from `run` is
an illegal move.

`Card` (`card_utils.py`): `Card.parse("AS")`, `card.rank` (`"2".."9"`, `"T"`, `"J"`,
`"Q"`, `"K"`, `"A"`), `card.suit` (`"C"`, `"D"`, `"H"`, `"S"`), `card.value`
(2 to 14, ace high), `str(card) == "AS"`. Compare ranks with `.value`, never
with the rank strings.

`GameStateView` (`game_state.py`): every name you can read. Lists come back as
tuples.

| The table | |
|---|---|
| `button`, `small_blind`, `big_blind` | player id; chips; chips |
| `stacks`, `bets`, `folded`, `acted` | per player: chips behind, chips bet this street, bools |
| `teams` | per player: team name. Seats change between hands, names do not: your team is `teams[self.player_id]`, a team's seat is `teams.index(name)` |
| `board` | Cards dealt so far |
| `street` | `"preflop"`, `"flop"`, `"turn"`, `"river"` |
| `pot` | every chip put in this hand, this street's bets included |
| `last_raise` | size of the last full raise this street |
| `to_act` | player id to act, `None` when the street is closed |
| `num_players`, `players_in_hand`, `active_players` | count; ids not folded; ids not folded and not all-in |
| `is_street_closed`, `is_hand_over` | bools |
| `current_bet` | the street total everyone must reach |

| Any player (methods) | |
|---|---|
| `status(player_id)` | `"active"`, `"folded"` or `"all_in"` |
| `effective_stack(player_id)` | their chips behind, capped by the biggest other stack still in (chips already bet excluded) |
| `spr(player_id)` | `effective_stack / pot` |

| The player to act (you, inside `run`) | |
|---|---|
| `call_amount` | chips a call adds (0 = check) |
| `min_raise_to`, `max_raise_to`, `raise_range` | legal raise totals; `raise_range` is `None` when you cannot raise |
| `can_raise` | bool |
| `legal_actions` | property, no `()`: e.g. `("fold", "call", "raise")` |
| `pot_odds` | `call_amount / (pot + call_amount)`, `0.0` when checking |
| `validate_action(action)` | raises `InvalidAction` if `action` is illegal |

`evaluate(cards)` (also in `card_utils.py`) takes 5 to 7 Cards (a list or tuple) and returns a tuple that
compares with `<`/`>` (higher wins, equal splits); `category(rank)` names it,
e.g. `"full_house"`. `rank[0]` indexes `CATEGORIES`, from `0` (high card) to
`8` (straight flush). Typical use: `evaluate(self.hand + list(game_state.board))`.

### Example bot

```python
from card_utils import evaluate
from player_action import PlayerAction


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, game_state):
        me = self.player_id
        if len(game_state.board) >= 3:
            # Two pair or better (see card_utils.CATEGORIES).
            strong = evaluate(self.hand + list(game_state.board))[0] >= 2
        else:
            high, low = sorted(card.value for card in self.hand)[::-1]
            strong = high == low or low >= 10  # a pair, or two high cards
        if strong and "raise" in game_state.legal_actions:
            low, high = game_state.raise_range
            # Raise to about the pot, kept within the legal range.
            return PlayerAction.raise_to(me, min(high, max(low, game_state.pot)))
        if game_state.call_amount == 0 or game_state.pot_odds < 0.25:
            return PlayerAction.call(me)
        return PlayerAction.fold(me)
```

### Testing locally

`engine.play_hand` plays one hand exactly as the organiser's engine does and
pays out the pot. From the repo root:

```python
import random
from card_utils import full_deck
from engine import play_hand
from model import Model

deck = full_deck()
random.Random(1).shuffle(deck)  # same seed, same cards
result = play_hand([Model(), Model()], deck, stacks=[500, 500])
print(result.net, [str(c) for c in result.board], result.faults)
```

`result.net` is chips won or lost per seat. `result.faults` maps a seat to
why its move was replaced: an exception in `seed` or `run`, or an illegal
move, is not fatal; the engine checks (nothing owed) or folds for you
instead. An empty `faults` means your bot played legally.

### Checking and measuring your bot

Install the test tools once: `.venv/bin/pip install -r requirements-dev.txt`.

- `.venv/bin/python -m pytest tests/test_model.py -q`: is `model.Model` a
  valid submission? It is pickled and played from that pickle in its own
  worker process, as in the arena, against every dummy bot: no faults
  allowed (exceptions, illegal moves, or a move over the 10 s limit). A
  failure prints the fault.
- `.venv/bin/python -m evals`: how strong is it? Plays your model heads-up
  against each dummy bot and prints your bb/100 (big blinds won per 100
  hands), its 95% confidence interval and a verdict (`ahead`, `behind`, or
  `unclear` when the interval includes 0). Options: `--opponents tight
  bots/team_b/team_b.pkl` (dummy names and other teams' pickles),
  `--deals 1000`, `--times 3` (repeat with new decks), `--table` (one table
  with everyone), `--model module:Class`, `--name`.

Dummy bots (`arena/dummies.py`): `fold`, `call`, `random`, `shove` (simple:
you should beat `fold` clearly, but `shove` and `random` punish bots that
play too tight or passively) and `tight`, `equity` (simple but sensible
play). Expect wide intervals: at 500 deals they are often ±50-100 bb/100.
Beating the dummies does not predict a tournament result: that depends on
the other teams' bots and the scoring, which are not known.

Every deck is replayed once per seat rotation, which reduces card luck
(roughly 0-40% narrower intervals), it does not remove it. Your model is
rebuilt from its pickle for each rotation and keeps its memory within one
(e.g. to learn opponents by `teams`).

### Submitting

`.venv/bin/python -m submit --team myteam` (Windows: `.venv\Scripts\python`)
pickles `Model()` with plain `pickle.dump` to `myteam.pkl`, lists the files to
upload (the `.pkl` and the `.py` files it needs; the pickle stores the class by
reference, so the code must come with it), shows what the organisers' code
review will flag, and plays the model from a clean copy of just those files.
Teams upload every file it lists in the Google Form; the organisers read the
code before anything runs. A pickle may only name the team's own modules and
plain data; a team `.py` file may not be named like a standard library or
arena module (`random.py`, `engine.py`), nor sit in a folder.

### Common mistakes

- Reading `call_amount`, `min_raise_to`, `can_raise`, `legal_actions`,
  `raise_range` or `pot_odds` outside your turn: they describe `to_act`.
- Calling `legal_actions()` with brackets: it is a property.
- Mutating `board` or `stacks`: they are tuples; copy with `list(...)`.
- Treating `raise_to(x)` as "raise by x".
- Forgetting to store `hand` and `player_id` in `seed`.
- Treating every `reveal_card` as a board card: players' shown cards at a
  showdown have their own `player_id`, the board's have `DEALER`.
- Wiping learned data between hands (e.g. re-running `__init__` in
  `end_game` or `seed`): keep what you learn in attributes `seed` does not
  overwrite.
- Passing card strings where Cards are expected: use `Card.parse("AS")`.
- Running with the system `python3` instead of `.venv/bin/python`.

### Not decided yet

Do not assume an answer to these; ask the user, who can ask the organisers:
the match format (heads-up or more players, number of hands, duplicate
deals), what happens after an `InvalidAction` (the local engine checks or
folds for you), time limits per move, and whether a big blind posted all-in for less still makes
others call the full big blind (currently they only call what was posted).

## For maintainers

- This repo is the source of truth for the arena. The older
  `poker_bot_infrastructure` repo is reference only (its results site was
  ported into `site/`, adapted to this engine; never port its live ladder,
  SQLite or websocket code).
- Event tooling (the organiser's side, on top of the arena): `submit.py`
  (teams), `arena/vet.py` (what a pickle may name, what the code is flagged
  for; a child process reads the pickle with every global stubbed, so nothing
  in it runs), `arena/event.py` (`python -m arena.event`: import, review,
  approve, run, export; state under `event/`), `arena/replays.py` (a played
  hand in the compact form the site replays), `arena/site_export.py` (results
  site with an artificial delay and spacing so it feels live). The pages themselves (`site/`, `site-src/`, `tests/site_js/`) and the
  Home page text (`event.json`) are the organiser's and are gitignored: they
  are not in this repository. The round is one
  heads-up round robin, each pairing a duplicate match; best bb/100 wins.
- The worker and remote model run on Windows, macOS and Linux (replies are
  read by a thread, not `select`; the process tree is killed with `taskkill`
  on Windows). The memory limit (`RLIMIT_AS`) applies on Linux only.
- Scope: pickled `Model` submissions, bot-vs-bot match evaluation, action
  validation, and the arena (`arena/`): matches between team pickles in
  sandboxed workers, raw results, bankroll and checkpoints. Simplicity
  over features; no database, website or live ladder.
- Architecture: one shared `GameState` per hand, owned by the engine
  (`engine.py`). Its `process_action` validates betting actions and
  `deal_card` before changing anything. Models only ever see a
  `GameStateView` of it; `GameStateView.PUBLIC` is the whitelist of what
  they may read. Private information (hole cards, player id) lives in
  `GameInterface` and the model, never in `GameState`.
- The view stops mistakes, not cheating. In the same process a model can
  change the shared state (`view._state`), rewrite `GameStateView.PUBLIC`,
  read every hole card and the deck through `sys._getframe` or `gc`, and
  edit Cards and actions with `object.__setattr__`. Per-seat copies would
  not fix this. So team models run in `arena.remote.RemoteModel` workers
  (`arena/worker.py`): one process each, a memory limit, a per-call
  timeout (a timed-out or crashed worker stays dead: check/fold for the
  rest of the match), stdout and stdin redirected, and the parent never
  unpickles anything a worker sends (replies are size-limited JSON). A
  separate process is not a sandbox: for real submissions run workers
  under `--launcher` (e.g. bwrap or a container with no network and a
  read-only filesystem). `engine.play_hand` alone is in-process and only for
  trusted play.
- `engine.play_hand` never trusts a returned action: anything not exactly a
  `PlayerAction` is a fault, and the rest is rebuilt from its fields and
  validated before it is applied or shown to other seats.
- The engine routes turns: `GameInterface` assumes it is only called when
  its player is `to_act`, and the engine applies the returned action.
  `engine.play_hand` is one hand only; match format (seating, button
  rotation, duplicate deals, carrying stacks) belongs to its caller.
- Layers: `engine` (one hand) <- `arena` (orchestration) <- `evals`
  (scoring). `arena.match.play_match(players, MatchConfig) -> MatchLog`
  builds models (`arena.players.build`: `RemoteModel` for a `.pkl`, a
  dummy in-process) and runs one match:
  - `mode="duplicate"` (evals): every deck once per seat rotation, fresh
    models per rotation, seating shuffled from the seed (3+ players),
    stacks reset every hand.
  - `mode="tournament"` (the real thing): one model per team for the whole
    match, a new deck every hand, the button moves, stacks carry over
    (from `MatchConfig.stacks`, e.g. the bankroll file), busted teams are
    out, and every model with a `checkpoint` is saved to
    `checkpoint_dir/<team>.pkl` at the end (on by default).
  `MatchLog` holds raw per-hand facts (`HandLog`: seats, net, faults), no
  statistics. `arena.results.ResultStore` appends logs as JSON lines;
  `load_bankroll`/`save_bankroll` keep team chips. `evals.score.score` and
  `leaderboard` turn logs into bb/100 with CIs; evals receives arena's
  `repeat_match` as a parameter (`evals.__main__.measure`), so its scoring
  never depends on how matches are run.
- CLIs: `python -m arena PLAYER...` (folders, pickles, dummy names;
  `--mode`, `--format table|round-robin`, `--bankroll`, `--results`,
  `--checkpoints`, `--launcher`) and `python -m evals` (`--leaderboard
  RESULTS` ranks an arena results file).
- `engine.py`, `card_utils.py`, `arena/` (except the CLI and dummies) and
  `evals/stats.py`, `evals/score.py` are covered by `tests/` (pytest +
  Hypothesis, pinned in `requirements-dev.txt`; arena tests in
  `tests/arena_tests/`, about 30 s in total). Keep them passing.
  `tests/test_model.py` runs the team's own `Model`.
- A new `GameState` query must also be added to `GameStateView.PUBLIC` and
  to the API tables above.
- Validation uses Pydantic v2 in strict mode, and only at entry points:
  `Card` and `PlayerAction` are strict Pydantic dataclasses (not
  `BaseModel`, so positional construction keeps working), and
  `setup_hand`, `set_stack`, `deal_card`, `GameInterface.__init__` and
  `evaluate` use `@validate_call`. Types do per-argument checks; checks
  between arguments (button < players, small blind <= big blind, duplicate
  cards) stay hand-written. Never validate `process_action` or the queries:
  they are the hot path.
- `GameInterface` does not validate `model` (`SkipValidation`): any object
  with `seed` and `run` is a bot.
- Keep `pydantic` pinned exactly in `requirements.txt`: pickles of Pydantic
  objects are only guaranteed to load under the version that made them, and
  the engine and every team must match.
- The files marked `DO NOT CHANGE THIS CODE` are the public contract: any
  change to a name or signature in them breaks submitted bots, so call it
  out to the user before making it.
- Commit to `main` only when asked, using jj (the repo is colocated);
  never push unless asked.

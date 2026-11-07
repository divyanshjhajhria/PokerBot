# ByteClub — heads-up no-limit hold'em bot

Team **ByteClub** (`byteclub`) for the Manchester AI Society poker bot hackathon,
7 November 2026. The engine, rules and tooling are the organisers'
([Manchester-AI-Society/poker_bot](https://github.com/Manchester-AI-Society/poker_bot);
their setup guide and API are in [CLAUDE.md](CLAUDE.md)). This repo is a copy of it with
our bot, our test and measurement tools, and a log of every experiment.

## The format, and what it means for the bot

Heads-up round robin; each pairing is a duplicate match (200 deals, seats swapped); 500 chips
at 10/20 (25 bb), **stacks reset every hand**; the best **bb/100** wins. So every decision is
pure chip EV (no survival or ICM), the opponent can be learned for ~200 hands per rotation,
and a timed-out move kills the bot for the rest of the match (10 s limit; we stay under 0.3 s).

## The submitted bot (`model.py` + `preflop_tables.py`, pickled as `byteclub.pkl`)

- **Preflop:** baked heads-up equity tables (169 hands vs random and vs top-X% ranges);
  exploit modes per opponent — min-raise into folders, value-raise stations, an EV choice of
  open size / jam against jammers, EV-based 3-bet jams using the opponent's learned
  fold-to-jam rate and jam-calling range width.
- **Postflop:** Monte Carlo equity (wall-clock capped at 0.3 s, range capped at 300 combos)
  against a villain range rebuilt **street by street from their betting line** (a bet keeps
  their stronger hands, a call drops their weakest), widened by how often their shown
  aggression was a bluff, and replaced by the **empirical distribution of hands they have
  shown** once there are 8+ showdowns.
- **Bet sizing:** an EV search over 1/3, 0.6 and 1x pot (and a jam when stacks are short),
  each size with its own learned fold rate (small ≤ 0.42 pot, mid ≤ 0.8, huge) and its own
  calling range, read back out of the same Monte Carlo samples.
- **Opponent profile** (Beta-smoothed, per team name): VPIP/PFR, fold to small raises / jams /
  bets by size, jam rates, aggression, showdown hands; labels for folder, station, maniac,
  and *random* (loose with erratic, non-all-in overbets — played as any two cards).

## How we tested it

Every change was an experiment against a frozen control, decided by a **paired A/B**
([sim/ab.py](sim/ab.py)): both bots play identical decks from identical seats against
opponents rebuilt with the same seed every hand, and the verdict is the 95% CI of the
per-deal difference. A change shipped only if nothing got worse beyond its CI and the
realistic group (ExampleBot, TAG, equity, JamOver, tight) stayed non-negative. The full log,
including rejected ideas, is in **[RESULTS.md](RESULTS.md)**.

The sparring pool ([sim/bots/sparring.py](sim/bots/sparring.py)) adds bots that imitate a
student field to the organisers' dummies: the CLAUDE.md example bot and three variants, TAG,
jam-over-raise, limp-shove, min-raiser, an "LLM-GTO" chart bot and an anti-AI overbettor.

Final checks on the submitted version:

| check | result |
|---|---|
| Round robin, 20 entrants, 200 deals each ([sim/roundrobin.py](sim/roundrobin.py)) | **1st, +167.4 ± 15.4 bb/100**; 0 faults |
| Fault audit, 2000 hands x 18 opponents, short stacks / all-in for less ([sim/fault_audit.py](sim/fault_audit.py)) | 0 faults, 0 hidden exceptions |
| Official evals at `--timeout 0.5` | 0 faults, slowest move 0.15 s |
| `tests/test_model.py`, `python -m submit` from a clean copy | 9/9, no review findings |

Round robin top four: ByteClub (final) +167.4, ByteClub (earlier upload) +151.8, the bot we
started from +126.9, our first independent bot +123.9; best sparring bot (LLM-GTO) +62.8.

What did **not** ship, and why, is in RESULTS.md — e.g. a single fold rate for every bet over
42% pot leaked ~45 bb/100 to a pot-odds caller (fixed by the three size classes above), and
an "information floor" was never needed against the anti-AI bot.

## Layout

```
model.py, preflop_tables.py   the bot (upload these + byteclub.pkl)
RESULTS.md                    every experiment, with paired CIs and verdicts
sim/ab.py                     paired whole-bot A/B decider
sim/roundrobin.py             parallel round robin, scored with evals.score.leaderboard
sim/fault_audit.py            2000-hand short-stack fault audit
sim/bots/                     sparring bots (+ make.py to pickle them)
sim/snapshots/                every frozen version used as an A/B control
sim/guh/                      the bot we started from (+ its table generator)
sim/ours/                     our first bot: 7-card evaluator, equity tables, profiler, strategy
tests/test_bot_*.py           tests for sim/ours (79)
card_utils.py engine.py game_*.py player_action.py arena/ evals/ submit.py
                              the organisers' engine and tools, unchanged
```

## Run it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/test_model.py -q          # valid submission?
.venv/bin/python -m evals                                   # bb/100 vs each dummy
.venv/bin/python sim/bots/make.py                           # build sparring pickles
.venv/bin/python sim/ab.py --cand model.py --base sim/snapshots/zip_s3.py
.venv/bin/python -m submit --team byteclub                  # build + check the upload
```

Built during the hackathon with Claude Code.

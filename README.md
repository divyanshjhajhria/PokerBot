# ByteClub poker bot

A heads-up no-limit Texas hold'em bot built in one day for the Manchester AI Society poker
bot hackathon (Great Uni Hackathon, 7 November 2026), submitted as team `byteclub`.

The game engine, rules and tooling are the organisers'
([Manchester-AI-Society/poker_bot](https://github.com/Manchester-AI-Society/poker_bot)); their
setup guide and API reference are in [CLAUDE.md](CLAUDE.md). Everything else here is ours.

## Submission

The submitted bot is these three files at the root of this repo:

| File | What it is |
|---|---|
| `byteclub.pkl` | the bot object, pickled by `python -m submit --team byteclub` |
| `model.py` | its code: `Model` with `seed`, `run` and `end_game` |
| `preflop_tables.py` | precomputed equity tables, imported by `model.py` |

The pickle was built from this exact `model.py`. To rebuild and check it the way the organisers
do: `.venv/bin/python -m submit --team byteclub`.

## Team

- Krishay
- [Utkarsh Jain](https://github.com/dragenair) ([LinkedIn](https://www.linkedin.com/in/utkarsh-jain-b073b2292/))
- [Divyansh Jhajhria](https://github.com/divyanshjhajhria)
- Shiven
- Riddhiman

## The format

Every bot plays every other bot heads-up. Each pairing is a duplicate match: 200 deals, each
dealt twice with the seats swapped, so card luck mostly cancels. Both players start every hand
with 500 chips at 10/20 blinds (25 big blinds), and the bot with the best **big blinds won per
100 hands (bb/100)** wins. A move has 10 seconds; a timeout knocks the bot out of the rest of
that match.

Because stacks reset every hand, there is no survival to worry about: every decision just
maximises expected chips. And because the same bot object plays a whole rotation (~200 hands),
it can learn each opponent and exploit them.

## How the bot works

The bot is `model.py` (with its precomputed tables in `preflop_tables.py`).

**Preflop.** Precomputed heads-up equities for all 169 starting hands, against a random hand
and against the top 5-100% of hands. Against each opponent it picks a style: min-raise
everything into players who fold too much, raise bigger for value against players who call too
much, and against players who shove over raises it compares the EV of folding, opening 2 / 2.5 /
3 big blinds and shoving. Re-shoves are an EV decision using how often that opponent folds to
an all-in and which hands they call one with.

**After the flop.** Monte Carlo equity against the opponent's likely hands. That range is
rebuilt street by street from what they actually did (a bet keeps their stronger hands, a call
drops their weakest), widened when their past bets turned out to be bluffs, and replaced by the
hands they have actually shown once there are 8 or more showdowns. Sampling stops at 0.3 s, so a
move never comes close to the time limit.

**Bet sizing.** For each possible bet (1/3, 0.6 or a full pot, or all-in when stacks are short)
it estimates how often this opponent folds to that size, and its equity against the hands that
would call it, then bets the size with the best expected gain over checking (or checks).

**Opponent profile.** For each opponent, by team name: how often they play and raise preflop,
fold to small raises / all-ins / small / medium / large bets, shove, bet, and what they show
down. Every rate starts from a sensible prior and moves with the evidence. Opponents who raise
random amounts are recognised and played as holding any two cards.

## How we tested it

Every change was an experiment against a frozen copy of the previous version, using a
**paired A/B test** ([sim/ab.py](sim/ab.py)): both versions play identical cards from identical
seats against opponents that make the same random choices, and the result is the difference per
deal with a 95% confidence interval. A change was kept only if no opponent got worse beyond the
margin of error. The full log, including the ideas we rejected and why, is in
[RESULTS.md](RESULTS.md).

Besides the organisers' simple dummy bots (fold, call, random, shove, tight, equity) we wrote
sparring bots that imitate what other teams were likely to submit
([sim/bots/sparring.py](sim/bots/sparring.py)): the example bot from the organisers' guide and
three variants, a tight-aggressive bot, a "GTO-style" chart bot of the kind an LLM writes, a
bot built to beat equity-based bots, and shove / min-raise / limp-shove bots.

## Results

Round robin of 20 bots, played exactly like the event (200 deals per pairing):

| Rank | Bot | bb/100 |
|---|---|---|
| 1 | **ByteClub (final version)** | **+167.4 ± 15.4** |
| 2 | ByteClub (earlier version) | +151.8 ± 15.1 |
| 3 | The open-source bot we started from | +126.9 ± 14.4 |
| 4 | Our first independent bot | +123.9 ± 15.6 |
| 5 | "GTO-style" chart bot | +62.8 ± 16.8 |
| 6 | Tight-aggressive bot | +49.9 ± 11.6 |
| | The organisers' example bot and its variants | -6.1 to +22.6 |

Against the organisers' dummy bots (`python -m evals`, 1000 hands each): fold +75,
call +455, random +399, shove +166, tight +54, equity +228 bb/100, all with zero faults.

Robustness: zero faults over 36,000 hands of stress tests (short stacks, all-ins smaller than a
raise, blinds posted all-in), and a worst-case move of 0.15 s when tested at a 0.5 s limit.

## Repository layout

```
model.py, preflop_tables.py   the bot (submitted with byteclub.pkl from `python -m submit`)
RESULTS.md                    log of every experiment and decision
sim/ab.py                     paired A/B test between two versions of the bot
sim/roundrobin.py             round robin of many bots, scored like the event
sim/fault_audit.py            stress test for crashes, illegal moves and slow moves
sim/bots/                     sparring bots (make.py builds their pickles)
sim/snapshots/                every frozen version used as a baseline
sim/guh/                      the bot we started from
sim/ours/                     our first independent bot (evaluator, equity tables, strategy)
tests/test_bot_*.py           tests for that first bot
card_utils.py, engine.py, game_*.py, player_action.py, arena/, evals/, submit.py, CLAUDE.md
                              the organisers' engine and tools, unchanged
```

## Running it

Python 3.10+.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest tests/test_model.py -q    # is it a valid submission?
.venv/bin/python -m evals                             # bb/100 against each dummy bot
.venv/bin/python sim/bots/make.py                     # build the sparring bots
.venv/bin/python sim/ab.py --cand model.py --base sim/snapshots/zip_s3.py
.venv/bin/python -m submit --team byteclub            # build and check the upload
```

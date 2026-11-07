# Results log

Every run: official `python -m evals`, seed 0 (same decks for every bot),
600 duplicate deals x 2 seats = 1200 hands per opponent, bb/100 with 95% CI.
Opponents: arena dummies plus the sparring bots in `sim/bots/sparring.py`.

## 2026-11-07 13:06 — our bot vs the zip bot (guh_pokerbot.zip)

"ours" = `model.py` (strategy.PokerBot). "zip" = the single-file bot from
`guh_pokerbot.zip`, run from an identical copy of the engine.

| Opponent | ours | zip | gap |
|---|---|---|---|
| ExampleBot | +86.3 ± 8.4 | +89.5 ± 9.7 | tie |
| TAGBot | +47.9 ± 20.1 | +50.7 ± 18.3 | tie |
| JamOverRaiseBot | +17.3 ± 30.2 | +40.7 ± 19.4 | zip +23 |
| LimpShoveBot | +107.0 ± 15.9 | +29.5 ± 46.5 | ours +77 |
| MinRaiseBot | +288.9 ± 63.2 | +438.4 ± 52.0 | zip +150 |
| call | +347.0 ± 57.1 | +436.8 ± 47.5 | zip +90 |
| equity | +139.6 ± 42.9 | +206.3 ± 36.7 | zip +67 |
| random | +274.5 ± 70.7 | +74.5 ± 42.3 | ours +200 |
| shove | +171.2 ± 70.1 | +188.2 ± 70.2 | tie |
| tight | +77.7 ± 11.7 | +51.7 ± 11.3 | ours +26 |
| fold | +75.0 ± 0.0 | +75.0 ± 0.0 | tie |
| sum | 1632 | 1681 | even |

Head-to-head, ours vs zip: **-1.8 ± 35.2 bb/100** (1200 hands, unclear).
Faults: 0 everywhere. Slowest move: ours 0.14 s, zip 0.27 s.

Reading: equal overall, different strengths. The zip bot extracts more from
callers (call, MinRaise, equity) with bigger value bets and fold-by-size
stats; ours is far better against random play and limp-shovers and better
against tight play.

Reproduce: `sim/bots/make.py` builds the sparring pickles; the zip bot's
pickle is `sim/guh/guh.pkl` (its own `model.py` sits next to it).

## 13:30 — Port 1: open/iso size chosen by EV (sim/ab.py, paired, 400 deals x 2 seats)

Control `strategy_v1`. Candidate picks the SB open from 2/2.5/3 bb and the
BB raise over a limp from 3/3.5/4 bb by EV.

| variant | ExampleBot | tight | TAG | equity | JamOver | shove | others |
|---|---|---|---|---|---|---|---|
| 1a: sizes vs everyone (seed 1) | -4.1 ± 2.2 | -3.9 ± 1.9 | +6.3 ± 11.0 | -5.8 ± 28.2 | **+45.3 ± 31.4** | -32.1 ± 24.8 | ~0 |
| 1a, seeds 2/3 | -4.6 / -5.5 | -2.4 / -4.9 | -0.3 / +0.2 | +7.3 / +8.0 | +33.7 / +31.2 | +4.7 / +4.2 | |
| 1b: sizes only vs jammers (shove-over-raise >= 0.15) | 0 | 0 | 0 | 0 | +5.1 ± 1.1 | -32.2 | 0 |
| **1b': jammer threshold 0.10 (promoted)** | **0** | **0** | **-0.1** | **0** | **+49.4 ± 31.2** | -32.2 ± 24.8 | 0 |
| 1b', seeds 2/3/4 | | | | | +32.5 / +29.9 / -0.2 | +4.6 / +4.2 / +6.1 | |

Size-independent response estimates make the EV min-raise every steal, which
costs ~5 bb/100 against ExampleBot and tight; restricted to jammers it keeps
the JamOver gain and leaves every other opponent bit-identical. The seed-1
ShoveBot -32 does not replicate (heavy-tailed all-in pots).

## 13:36 — Port 2: fold-by-size stats + EV over postflop bet sizes (paired, 400 deals x 2 seats)

Control `strategy_v2` (Port 1). Candidate tracks fold-to-small (<= 42% pot)
vs fold-to-big bets and picks the bet from 33% / 60-75% / 100% pot (+ all-in
at SPR <= 3) by EV, each size with its own fold rate and calling range.

| seed | call | random | ExampleBot | TAG | tight | equity | JamOver | MinRaise | LimpShove | fold/shove |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **+17.3 ± 13.6** | +28.6 ± 29.9 | **+3.8 ± 3.8** | +5.7 ± 8.7 | +0.3 ± 0.6 | -4.7 ± 30.2 | -4.3 ± 14.0 | +2.8 ± 12.8 | +3.4 ± 5.6 | 0 |
| 2 | +12.5 ± 12.6 | +2.3 ± 35.6 | -0.9 ± 3.7 | +0.5 ± 0.7 | -0.4 ± 1.6 | +2.8 ± 23.8 | 0.0 | -10.7 ± 10.7 | 0.0 | 0 |

Promoted: consistent gain against callers (~+15), neutral on the realistic
group (ExampleBot, TAG, tight, equity, JamOver within ±3 pooled), nothing
reliably worse (MinRaise pooled -4 ± 8).

## 13:40 — decision run: official evals, seed 0, 600 deals x 2 seats

| Opponent | ours v1 (13:06) | ours v3 (both ports) | zip |
|---|---|---|---|
| ExampleBot | +86.3 ± 8.4 | +86.6 ± 9.4 | +89.5 ± 9.7 |
| TAGBot | +47.9 ± 20.1 | +44.1 ± 20.2 | +50.7 ± 18.3 |
| equity | +139.6 ± 42.9 | +110.6 ± 43.7 | +206.3 ± 36.7 |
| JamOverRaiseBot | +17.3 ± 30.2 | +19.6 ± 27.9 | +40.7 ± 19.4 |
| tight | +77.7 ± 11.7 | +78.0 ± 11.5 | +51.7 ± 11.3 |
| LimpShoveBot | +107.0 ± 15.9 | +109.3 ± 15.3 | +29.5 ± 46.5 |
| MinRaiseBot | +288.9 ± 63.2 | +290.8 ± 62.6 | +438.4 ± 52.0 |
| call | +347.0 ± 57.1 | +357.0 ± 56.8 | +436.8 ± 47.5 |
| random | +274.5 ± 70.7 | +208.7 ± 68.1 | +74.5 ± 42.3 |
| shove | +171.2 ± 70.1 | +168.1 ± 70.4 | +188.2 ± 70.2 |
| fold | +75.0 | +75.0 | +75.0 |
| **total (11)** | 1632.4 | **1547.8** | **1681.3** |
| realistic 5 | 368.8 | 338.9 | 438.9 |

Head-to-head ours v3 vs zip: +5.6 ± 34.9. Rule 1 (higher 11-opponent total
wins): **zip bot is the candidate**. Note: these runs are not paired (random
and equity dummies' RNG streams diverge once decisions differ), so each total
carries roughly ±140 of noise; v1 -> v3 here is noise per the paired A/B.

## 14:00 — base switched to the zip bot; whole-bot paired decider (sim/ab.py)

`model.py` + `preflop_tables.py` at the root are now the zip bot (v0 frozen in
`sim/guh/`). Our previous bot is frozen in `sim/ours/` (`ours_model.py`,
opponent pickle `sim/ours/ours.pkl`; strategy == `sim/snapshots/strategy_v3.py`).
From here on the decider is `sim/ab.py`: both whole bots, identical decks and
seats, opponents rebuilt with the same seed every hand, CI on the per-deal
difference. Validated: zip vs an identical copy gives exactly 0.0 on every
opponent (25 deals x 4 opponents).

Decider, ours (frozen) vs zip v0, 400 deals x 2 seats, seed 1:

| ExampleBot | TAG | equity | JamOver | tight | LimpShove | MinRaise | call | random | shove | fold |
|---|---|---|---|---|---|---|---|---|---|---|
| +10.1 ± 12.1 | +9.3 ± 20.2 | **-82.8 ± 76.8** | -20.3 ± 27.4 | **+32.7 ± 17.0** | +40.5 ± 75.2 | **-137.8 ± 64.2** | -60.6 ± 88.9 | **+189.7 ± 97.7** | -11.6 ± 28.8 | 0 |

Realistic group -51.0 ± 86.6. Confirms the switch.

Diagnosis for port C (zip v0 vs RandomBot, 400 hands): facing RandomBot's
preflop raises zip folded 90 of 108 times; 59 of those folds had equity vs a
random hand above the price (22, 33, A2o-A4o, 98s...). Its raise range is
modelled as top-PFR%; ours labels RandomBot 'random' from erratic sizing
(non-all-in overbets) and plays it as any two cards.

## 14:04 — Port A: 0.3 s wall-clock cap on Monte Carlo (zip_pA vs zip v0, decider, seed 1)

Bit-identical on 9 of 11 opponents; the cap only bound against equity
(+2.2 ± 6.0) and MinRaise (-3.0 ± 5.3). Realistic group +2.2 ± 6.0. But the
slowest move was 0.994 s (vs MinRaise) and 0.914 s (vs equity): Monte Carlo
is capped, but before sampling the zip bot sorts its whole range (~1000
combos) with the engine's slow evaluate (~220 us each), uncapped. Not
promoted on timing; replaced by A2 below.

A2 = A + the villain range capped at 300 random combos before ranking and
sampling. B2 = A2 + port B with lazy, per-hand cached ranking (only combos
actually used are evaluated). C2 = B2 + port C. Snapshots in sim/snapshots/.

## 14:16 — port chain on the decider (400 deals x 2 seats, seed 1)

| step | ExampleBot | TAG | equity | JamOver | tight | LimpShove | MinRaise | call | random | shove/fold | realistic | slowest | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A2 vs v0 | -3.7 ± 10.8 | +0.3 ± 11.1 | -3.0 ± 17.1 | -0.8 ± 1.9 | -0.1 ± 0.2 | +19.4 ± 33.1 | +5.7 ± 18.5 | +0.3 ± 14.6 | +6.6 ± 18.9 | 0 | -7.4 ± 23.2 | 0.151 s (was 0.994) | PROMOTE |
| B2 vs A2 | +0.4 ± 8.8 | -2.5 ± 9.5 | +8.9 ± 21.1 | +2.3 ± 3.0 | +0.1 ± 0.2 | 0 | -16.7 ± 19.1 | -4.7 ± 14.5 | -10.7 ± 24.6 | 0 | +9.2 ± 24.9 | 0.119 s | PROMOTE |
| C2 vs B2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **+219.7 ± 71.4** | 0 | 0.0 | 0.109 s | PROMOTE |

Root `model.py` == `sim/snapshots/zip_pC2.py`. Each promoted version passed
tests/test_model.py (9/9) and `python -m submit --team byteclub` (0 review
findings, played legally; upload set byteclub.pkl, model.py,
preflop_tables.py). EquityBot edge kept: -3.0, +8.9, 0.0 across the steps.

## 14:21 — cumulative check + EquityBot diagnosis (port D)

Decider, C2 vs zip v0 (400 deals x 2 seats, seed 1): random **+215.6 ± 70.8**
(better); ExampleBot -3.3 ± 6.7, TAG -2.2 ± 13.2, equity +5.9 ± 19.7,
JamOver +1.5 ± 2.9, tight 0.0, LimpShove +19.4 ± 33.1, MinRaise -10.9 ± 20.7,
call -4.4 ± 13.6, shove/fold 0 (all unclear). Realistic group +1.8 ± 24.8.
Slowest move 0.304 s. PROMOTE.

Why zip beats our old bot against EquityBot (same 800 hands, zip C2 - ours
= +88.7 bb/100): our bot shoved preflop in 370 of 800 hands and those hands
carry +93.8 bb/100 of the gap. It labels EquityBot a 'station' and its shove
EV assumes a station calls with any two cards (>= 90%, whole range);
EquityBot calls a shove only when its equity vs a random hand beats the
48% price, i.e. roughly its top 57%. The zip bot min-raises or limps
(raise 2bb +48.0, call +41.7 of the gap) and takes EquityBot's postflop
over-calls. None of the ports touch preflop shove logic, so the edge is kept
(+5.9 ± 19.7 cumulative).

## 14:25 — time limits (official evals, root = C2)

All 11 opponents, 200 deals x 2 seats, at `--timeout 1.0` and `--timeout 0.5`:
**0 faults in all 22 runs.** Slowest move at 0.5 s: 0.16 s (MinRaise), 0.15 s
(call, random), 0.14 s (equity); shove 0.00 s (all-in path is the table lookup).
The user's exact commands (shove, equity, ExampleBot, TAG; 300 deals x 2
seats) at 0.5 s and 1.0 s: 0 faults, slowest 0.09 s (equity).
Worker start-up + first call (arena RemoteModel, 6 cold starts): 71-75 ms;
pickle 50 KB, in-process unpickle 7 ms; warm call 0.2 ms.

## 14:28 — submission files (submit_ready/)

`python -m submit --team byteclub --out submit_ready` on root model.py
(== sim/snapshots/zip_pC2.py): 0 review findings, played legally from a clean
copy. Upload all three:

| file | sha256 |
|---|---|
| byteclub.pkl | 7968e7aad2773e36c04d1ad46faf77f88cad1c7c55bf855b451748a42585e19c |
| model.py | a3b33584cfe7dadb7c55ac28af8742eecf478752083d0f8323a01c2480c135fd |
| preflop_tables.py | 4e28a4c5da19da6e779787ebf48ca856a5edbe49d9224e44f5346ea4b3475758 |

## 14:35 — post-submission step 1: fold vs small raise / vs jam (zip_s1 vs C2, decider, seed 1)

New stats: fold to raises <= 3 bb, fold to all-ins, and the shown percentile of
hands that called our jams (width = 2 x mean shown percentile after >= 3,
since the mean of a uniform top-W range is W/2). Used as p_fold for 2-3 bb
opens (fold_vs_small) and for a new jam option in the open EV and an EV-based
light 3-bet jam (fold_vs_jam + calling width), replacing the f_3b >= 0.6 rule.

ExampleBot **+15.2 ± 9.4** (better), TAG +1.0 ± 19.8, equity 0.0, JamOver
-10.6 ± 33.8, tight +5.5 ± 10.8, LimpShove -5.7 ± 56.4, MinRaise -12.1 ± 13.7,
call 0.0, random +35.0 ± 44.0, shove -12.3 ± 41.7, fold 0. Realistic +11.1 ±
41.7; slowest 0.145 s. **PROMOTED** (champion = zip_s1).

## 14:40 — step 2: field priors (zip_s2 vs zip_s1, decider, seed 1)

Priors: fold_to_raise 0.55, fold_vs_jam 0.6, jam rates 0.03, VPIP 0.5, PFR
0.25; postflop fold_to_bet's prior mean = their learned preflop
fold_vs_small (weight 4), so with no data at all it starts near 0.55.

ExampleBot -3.2 ± 5.5, TAG +2.0 ± 16.3, equity +3.6 ± 19.5, JamOver +3.9 ±
7.1, tight +1.1 ± 3.6, LimpShove -15.0 ± 21.1, MinRaise +8.3 ± 19.2, call
**+19.5 ± 13.7** (better), random -11.2 ± 25.4, shove +2.9 ± 5.6, fold 0.
Realistic +7.3 ± 27.2; slowest 0.147 s. **PROMOTED** (champion = zip_s2).

## 14:44 — step 3: showdown range estimation (zip_s3 vs zip_s2, decider, seed 1)

Percentiles of shown hands kept per preflop action (raised / called); after
>= 8 in that category the postflop villain range is sampled from their
10-bin histogram (0.5 smoothing per bin) instead of top-X% by PFR/VPIP; the
street bands still apply. Jams, maniacs and random players unchanged.

ExampleBot -0.5 ± 4.3, TAG -4.5 ± 9.8, equity +5.7 ± 16.6, JamOver +2.6 ±
8.5, tight +0.3 ± 1.2, LimpShove 0, MinRaise +12.7 ± 27.2, call +6.6 ± 17.6,
random/shove/fold 0. Realistic +3.7 ± 21.6; slowest 0.123 s. **PROMOTED**
(champion = zip_s3). Small effect: tight bots rarely reach 8 showdowns per
category inside a rotation (TAG: 3 + 3 after 150 hands).

## 14:48-14:57 — step 4: postflop bet-size EV search

Gain of a bet over checking (checking already earns ~eq x pot): fold adds
pot x (1 - eq), a call adds (2 eq - 1) x size. (The requested formula
f x pot + (1 - f)(2 eq - 1) x size omits the eq x pot that checking already
earns, so it credits folding out a strong hand with the whole pot.)

4 (equity vs their whole range, sizes 0.33 / 0.6 / 1.0 pot / jam), vs zip_s3,
seed 1: ExampleBot +10.1 ± 7.8, call +69.7 ± 69.3 (better), but **equity
-67.7 ± 56.8 (worse)**: one fold rate for every big size + whole-range equity
makes the search jam whenever eq > 0.5, and EquityBot calls big bets only
with hands that beat the price. **Not promoted.**

4b (equity vs the hands that call that size: the top 1 - f of their range,
read from the same Monte Carlo samples by each villain combo's strength
quantile; jam only when stack <= 1.5 pot), vs zip_s3:

| seed | ExampleBot | TAG | equity | JamOver | tight | LimpShove | MinRaise | call | random | realistic |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | +6.7 ± 10.3 | +11.6 ± 21.1 | -4.8 ± 31.5 | **+13.6 ± 11.1** | -5.3 ± 12.1 | +26.1 ± 39.8 | -1.9 ± 21.7 | +15.4 ± 36.2 | +38.4 ± 47.4 | +21.8 ± 42.6 |
| 2 | **+11.2 ± 9.2** | +6.1 ± 16.0 | +27.4 ± 27.4 | -0.1 ± 2.4 | +5.2 ± 12.1 | +14.4 ± 29.5 | +12.1 ± 22.0 | +30.8 ± 38.7 | +34.0 ± 41.0 | **+49.8 ± 35.3** |

Slowest 0.142 / 0.240 s. **PROMOTED** (champion = zip_s4b). Steps 1, 2, 3, 4b
each passed tests/test_model.py (9/9) and submit --team byteclub (0 findings,
played legally, upload set unchanged).

## 15:05 — cumulative: zip_s4b (steps 1-4b) vs submitted C2 (decider, seed 1)

ExampleBot **+18.7 ± 13.4**, TAG +10.1 ± 30.7, equity +20.1 ± 29.7, JamOver
+10.4 ± 37.4, tight +4.1 ± 19.1, LimpShove -11.7 ± 68.9, MinRaise +10.1 ±
34.2, call **+48.0 ± 40.2**, random **+74.7 ± 63.4**, shove -9.4 ± 41.3, fold
0. Realistic **+63.4 ± 61.4**. Slowest 0.264 s (run alongside other checks).
**Beats the submitted version -> submit_ready/ rebuilt from zip_s4b at 15:06**
(test_model 9/9, submit: 0 findings, played legally). Old C2 files kept in
sim/archive/submit_C2/.

| file | sha256 |
|---|---|
| byteclub.pkl | 7968e7aad2773e36c04d1ad46faf77f88cad1c7c55bf855b451748a42585e19c (identical to C2's: the pickle holds only the start-of-match state; the changes are in model.py) |
| model.py | a137e2d58db8c1d80487e97351764221d4af2475f86a71b047add6d581ce0c07 |
| preflop_tables.py | 4e28a4c5da19da6e779787ebf48ca856a5edbe49d9224e44f5346ea4b3475758 |

## 15:10-15:14 — new sparring bots: AntiAI number, and an ExampleV1 regression

Decider, zip_s4b vs C2, seed 1: **AntiAIBot: champion +12.9 ± 57.9** (C2
-10.1; diff +23.1 ± 49.5) -> above the -15 trigger, so the information floor
was **not** A/B'd (its snapshot zip_s5 exists but is in neither the root nor
the submission). LLMGTOBot -2.7 ± 35.4, ExampleV2 +0.8 ± 31.0, ExampleV3
**+22.8 ± 7.0**, but **ExampleV1 -52.5 ± 35.2 (worse)**.

Replicated (seed 2): ExampleV1 **-40.3 ± 34.9** (ExampleV3 +15.8 ± 5.5,
ExampleBot +20.4 ± 10.2). By step, on ExampleV1: s1 +5.1 ± 15.1, s2 +7.9 ±
17.0, s3 -6.3 ± 22.0, **s4b -47.3 ± 33.4**. Cause: ExampleV1 calls any bet
under the pot (pot_odds < 0.33) and folds to pot-size bets; 4b's single "big"
fold rate lumps 0.6-pot and 1.0-pot bets, so it sometimes value-bets the pot
and folds ExampleV1 out.

s4b fails the rule on the expanded pool -> **submit_ready/ restored to C2 at
15:14** (checksums back to the C2 values above).

## 15:22 — s3 (steps 1-3) vs C2 on all 16 opponents (decider, seed 1)

| ExampleBot | TAG | equity | JamOver | tight | ExampleV1 | ExampleV2 | ExampleV3 | LLMGTO | AntiAI | LimpShove | MinRaise | call | random | shove | fold |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **+12.5 ± 10.5** | +1.9 ± 22.7 | +7.1 ± 20.5 | -3.7 ± 35.9 | +7.1 ± 11.3 | +5.1 ± 25.5 | +22.3 ± 26.6 | **+22.8 ± 7.0** | +16.3 ± 35.4 | +7.8 ± 49.9 | -33.1 ± 61.8 | +18.4 ± 27.5 | **+26.1 ± 17.6** | +23.7 ± 48.9 | -9.4 ± 41.3 | 0 |

Nothing worse; realistic +24.9 ± 49.6; slowest 0.330 s (run alongside other
checks). **Passes -> submit_ready/ rebuilt from s3 at 15:23 (final for the
16:00 upload).**

## 15:23 — upload files (submit_ready/ = s3)

Pickle regenerated by `python -m submit --team byteclub` from the repo root
with root model.py == submit_ready/model.py (s3). Clean copy (fresh
`git archive upstream/main` + exactly these three files): tests/test_model.py
9/9, submit check OK, vet on the uploaded pickle not refused / no findings
(names: card_utils.Card, model.Model, random.Random), validate OK.

| file | sha256 |
|---|---|
| byteclub.pkl | 7968e7aad2773e36c04d1ad46faf77f88cad1c7c55bf855b451748a42585e19c |
| model.py | 240e73f9b6f4a67ccc79060fd9045bcc55b90980411086ad6b9d7abbd2bf4f6d |
| preflop_tables.py | 4e28a4c5da19da6e779787ebf48ca856a5edbe49d9224e44f5346ea4b3475758 |

(The pickle bytes are the same for C2/s3/s4b: it stores the start-of-match
state from __init__, which no step changed; behaviour lives in model.py.)

## 15:37 — step 4: zip_s4c vs uploaded s3, all 16 opponents (decider)

s4c = s4b with fold rates by three bet sizes: small <= 0.42 pot, mid <= 0.8,
huge > 0.8 (mid/huge default to the old "big" rate until they have data).

| seed | ExampleBot | TAG | equity | JamOver | tight | ExampleV1 | ExampleV2 | ExampleV3 | LLMGTO | AntiAI | LimpShove | MinRaise | call | random | shove/fold | realistic | slowest |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | +5.9 ± 10.1 | +5.8 ± 21.2 | +2.2 ± 30.0 | +9.3 ± 10.4 | **+12.5 ± 9.4** | **+35.3 ± 34.0** | **+29.7 ± 28.6** | 0 | **+32.3 ± 31.6** | +0.4 ± 0.6 | +10.0 ± 39.4 | -10.0 ± 24.8 | +24.8 ± 36.6 | +35.4 ± 49.9 | 0 | +35.7 ± 40.6 | 0.211 s |
| 2 | **+11.3 ± 9.2** | +2.3 ± 16.4 | +24.6 ± 27.1 | +0.1 ± 2.0 | **+14.2 ± 12.2** | **+46.9 ± 35.2** | **+41.1 ± 28.4** | 0 | -6.5 ± 31.9 | -0.1 ± 3.2 | +6.2 ± 27.8 | -3.0 ± 26.4 | **+42.8 ± 39.1** | +22.7 ± 44.4 | 0 | **+52.5 ± 35.3** | 0.291 s |

Nothing worse on either seed; ExampleV1 better on both. **PROMOTED** (pending
the round robin and the fault audit before any re-upload). Slowest-move
figures here are under full 10-process load.

## 15:45 — step 5: round robin (sim/roundrobin.py), 20 entrants, 200 deals x 2 seats per pairing

190 pairings through arena.match.play_match (team pickles in workers),
scored by evals.score.leaderboard. 0 faults for every entrant.

| # | entrant | bb/100 | slowest |
|---|---|---|---|
| 1 | **byteclub_s4c** (re-upload candidate) | **+167.4 ± 15.4** | 0.31 s |
| 2 | **byteclub** (s3, uploaded) | **+151.8 ± 15.1** | 0.22 s |
| 3 | guh (zip v0) | +126.9 ± 14.4 | 0.43 s |
| 4 | ours (old bot) | +123.9 ± 15.6 | 0.15 s |
| 5 | LLMGTOBot | +62.8 ± 16.8 | |
| 6 | TAGBot | +49.9 ± 11.6 | |
| 7 | AntiAIBot | +34.4 ± 16.0 | |
| 8 | tight | +26.6 ± 10.2 | |
| 9 | ExampleV3 | +22.6 ± 12.4 | |
| 10 | equity | +17.2 ± 17.3 | |
| 11 | LimpShoveBot | +13.1 ± 15.4 | |
| 12 | ExampleV2 | -1.1 ± 13.2 | |
| 13 | ExampleBot | -4.8 ± 9.3 | |
| 14 | ExampleV1 | -6.1 ± 12.6 | |
| 15-20 | fold -46.5, JamOverRaise -48.9, shove -70.9, random -171.9, MinRaise -212.3, call -234.0 | | |

byteclub (s3) by opponent: ours -55.4, s4c -13.2, zip v0 -11.4, AntiAI +22.6,
TAG +31.7, JamOver +40.8, tight +47.3, fold +75.0, V1 +76.6, V3 +80.1,
ExampleBot +84.5, V2 +87.3, LLMGTO +166.2, LimpShove +186.5, shove +255.2,
equity +257.2, random +405.8, call +570.6, MinRaise +576.7 (one 400-hand
match each). Slowest moves here were measured with 10 pairings in parallel.

## 15:50 — step 6: fault audit, zip_s4c (sim/fault_audit.py)

2000 hands x 18 opponents (36,000 hands; per opponent ~718 full, ~354 blind
all-in for less, ~299 short all-ins below a min-raise, ~315 covered, ~314
both tiny): **0 faults, 0 swallowed exceptions; slowest move 0.201 s**
(ExampleV1, in-process, 10 processes in parallel).

Official evals at `--timeout 0.5`, organisers' workers, clean engine copy,
3 matches in parallel, 300 deals x 2 seats: random +342.8, call +550.5,
ExampleV1 +144.3, equity +253.8, MinRaise +525.2, LLMGTO +158.4 — **0 faults,
slowest 0.15 s**.

## 15:55 — s4c upload set staged in submit_next/ (submit_ready/ untouched = s3)

Pickle regenerated by `python -m submit --team byteclub` from the repo root
with root model.py == submit_next/model.py (s4c). Clean copy (fresh
`git archive upstream/main` + exactly these three files): test_model 9/9,
submit check OK, vet not refused / no findings (names: card_utils.Card,
model.Model, random.Random), validate OK.

| file | sha256 |
|---|---|
| byteclub.pkl | 7968e7aad2773e36c04d1ad46faf77f88cad1c7c55bf855b451748a42585e19c |
| model.py | 3276f1088bda63ed875916b3b5f2d7ce5011cf9a421908912bd3384c80c0b8e6 |
| preflop_tables.py | 4e28a4c5da19da6e779787ebf48ca856a5edbe49d9224e44f5346ea4b3475758 |

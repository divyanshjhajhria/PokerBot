"""Offline: 169 starting hands, heads-up equity vs random and vs top-X% ranges.
Output: preflop_tables.py (plain dict literal) to drop next to model.py."""
import eval7, random, json, sys

RANKS = "23456789TJQKA"
rng = random.Random(1)

def hand_key(a, b, suited):
    hi, lo = max(a, b, key=RANKS.index), min(a, b, key=RANKS.index)
    if hi == lo:
        return hi + lo
    return hi + lo + ("s" if suited else "o")

def all_keys():
    keys = []
    for i, r1 in enumerate(RANKS[::-1]):
        for j, r2 in enumerate(RANKS[::-1]):
            if i == j:
                keys.append(r1 + r2)
            elif i < j:
                keys.append(r1 + r2 + "s")
                keys.append(r1 + r2 + "o")
    return keys

def combos_for(key):
    suits = "cdhs"
    out = []
    if len(key) == 2:
        r = key[0]
        for i in range(4):
            for j in range(i + 1, 4):
                out.append((eval7.Card(r + suits[i]), eval7.Card(r + suits[j])))
    elif key[2] == "s":
        for s in suits:
            out.append((eval7.Card(key[0] + s), eval7.Card(key[1] + s)))
    else:
        for s1 in suits:
            for s2 in suits:
                if s1 != s2:
                    out.append((eval7.Card(key[0] + s1), eval7.Card(key[1] + s2)))
    return out

def equity_vs_combos(hero, villain_combos, n):
    """hero: tuple of 2 eval7 cards; villain_combos: list of tuples. MC."""
    deck = [c for c in eval7.Deck().cards if c not in hero]
    wins = 0.0
    valid = [v for v in villain_combos if hero[0] not in v and hero[1] not in v]
    if not valid:
        return 0.5
    for _ in range(n):
        v = valid[rng.randrange(len(valid))]
        rest = [c for c in deck if c not in v]
        board = rng.sample(rest, 5)
        h = eval7.evaluate(list(hero) + board)
        o = eval7.evaluate(list(v) + board)
        if h > o: wins += 1
        elif h == o: wins += 0.5
    return wins / n

keys = all_keys()
assert len(keys) == 169
# representative combo for each key (equity is suit-symmetric)
rep = {k: combos_for(k)[0] for k in keys}
all_combos = []
for k in keys:
    all_combos += combos_for(k)
assert len(all_combos) == 1326

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
vs_random = {k: equity_vs_combos(rep[k], all_combos, N) for k in keys}
order = sorted(keys, key=lambda k: -vs_random[k])
# percentile of each hand by combos (cumulative share of 1326 combos)
pct = {}
cum = 0
for k in order:
    cum += len(combos_for(k))
    pct[k] = cum / 1326
tops = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100]
vs_top = {}
for t in tops:
    rng_combos = []
    for k in order:
        if pct[k] <= t / 100 + 1e-9 or not rng_combos:
            rng_combos += combos_for(k)
    vs_top[t] = {k: equity_vs_combos(rep[k], rng_combos, N // 2) for k in keys}
    print("top", t, "done", len(rng_combos), "combos", file=sys.stderr)

with open("preflop_tables.py", "w") as f:
    f.write("# Generated offline: heads-up preflop equities (Monte Carlo).\n")
    f.write("VS_RANDOM = " + json.dumps({k: round(v, 4) for k, v in vs_random.items()}) + "\n")
    f.write("PCT = " + json.dumps({k: round(v, 4) for k, v in pct.items()}) + "\n")
    f.write("TOPS = " + json.dumps(tops) + "\n")
    f.write("VS_TOP = " + json.dumps({str(t): {k: round(v, 4) for k, v in d.items()} for t, d in vs_top.items()}) + "\n")
print("done")

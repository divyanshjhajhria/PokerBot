"""The submitted pickle must play with no data files and no disk access."""
import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OURS = os.path.join(ROOT, 'sim', 'ours')  # our frozen bot
sys.path.insert(0, ROOT)
sys.path.insert(0, OURS)
import bot  # noqa: E402

try:
    import ours_model as model  # needs pydantic (the engine's venv)
    import submit
    # Our modules exactly as submit.py lists them for upload.
    OWN = sorted(os.path.basename(p)[:-3] for p in submit.local_modules({'ours_model'}, OURS))
except ImportError:
    model, OWN = None, []

# Our code minus tables_data.py (deliberately absent), plus the engine's
# public modules that the clean-room child needs to play hands.
ENGINE = ['card_utils.py', 'player_action.py', 'game_state.py', 'game_interface.py', 'engine.py',
          'model.py', 'preflop_tables.py']   # engine.py imports the root model
FILES = [m + '.py' for m in OWN if m != 'tables_data'] + ENGINE

STATES = [
    {'hole': ['As', 'Kd'], 'board': [], 'pot': 30, 'to_call': 10, 'stack': 490, 'big_blind': 20},
    {'hole': ['7c', '2d'], 'board': [], 'pot': 60, 'to_call': 40, 'stack': 480, 'big_blind': 20},
    {'hole': ['Qh', 'Jh'], 'board': ['Th', '9c', '2h'], 'pot': 120, 'to_call': 0,
     'stack': 440, 'big_blind': 20},
    {'hole': ['Ad', '5d'], 'board': ['Kd', '9d', '4s', '2c'], 'pot': 300, 'to_call': 100,
     'stack': 250, 'big_blind': 20},
    {'hole': ['8s', '8h'], 'board': ['8d', 'Ks', '3c', 'Jh', '2s'], 'pot': 500, 'to_call': 0,
     'stack': 250, 'big_blind': 20},
]

CHILD = r'''
import builtins, json, pickle, random, sys
sys.path.insert(0, sys.argv[1])
try:
    import tables_data  # must NOT be reachable
    print('tables_data leaked onto sys.path'); sys.exit(2)
except ImportError:
    pass
with open(sys.argv[2], 'rb') as f:
    blob = f.read()
a, b = pickle.loads(blob), pickle.loads(blob)
from card_utils import full_deck
from engine import play_hand

def no_disk(*args, **kwargs):
    raise RuntimeError('disk access at play time')
builtins.open = no_disk

decisions = [a.core.act(s) for s in json.loads(sys.argv[3])]
faults, nets = [], []
for d in range(40):
    deck = full_deck()
    random.Random(d).shuffle(deck)
    r = play_hand([a, b], deck, [500, 500], button=d % 2)
    faults.append(r.faults)
    nets.append(r.net)
print(json.dumps({'decisions': decisions, 'faults': faults, 'nets': nets,
                  'stats': a.core.stats}))
'''


@unittest.skipIf(model is None, 'needs the engine venv (.venv/bin/python)')
class TestPickle(unittest.TestCase):
    def test_pickle_plays_without_data_files_or_disk(self):
        blob = pickle.dumps(model.Model(), protocol=4)   # as submit.py does
        expected = [pickle.loads(blob).core.act(dict(s)) for s in STATES]
        tmp = tempfile.mkdtemp()
        try:
            for name in FILES:
                src = os.path.join(OURS, name)
                shutil.copy(src if os.path.exists(src) else os.path.join(ROOT, name), tmp)
            pkl = os.path.join(tmp, 'team.pkl')
            with open(pkl, 'wb') as f:
                f.write(blob)
            out = subprocess.check_output(
                [sys.executable, '-I', '-c', CHILD, tmp, pkl, json.dumps(STATES)], cwd=tmp)
        finally:
            shutil.rmtree(tmp)
        res = json.loads(out)
        self.assertEqual(res['stats']['errors'], 0, res['stats']['last_error'])
        self.assertEqual(res['decisions'], expected)
        self.assertEqual(res['faults'], [{}] * 40)
        self.assertTrue(all(sum(n) == 0 for n in res['nets']))

    def test_pickle_names_only_allowed_globals(self):
        from arena import vet
        tmp = tempfile.mkdtemp()
        try:
            pkl = os.path.join(tmp, 'team.pkl')
            with open(pkl, 'wb') as f:
                pickle.dump(model.Model(), f, protocol=4)
            names = vet.pickle_globals(pkl)
        finally:
            shutil.rmtree(tmp)
        own = set(OWN)
        bad = [n for n in names if not vet._is_allowed(n, own, ())]
        self.assertEqual(bad, [], names)

    def test_pickle_size_reasonable(self):
        self.assertLess(len(pickle.dumps(model.Model())), 2 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()

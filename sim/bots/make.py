"""Pickles the sparring bots next to sparring.py: .venv/bin/python sim/bots/make.py"""
import os
import pickle
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(os.path.dirname(HERE)))
import sparring  # noqa: E402

for name in ('ExampleBot', 'MinRaiseBot', 'JamOverRaiseBot', 'TAGBot', 'LimpShoveBot',
             'LLMGTOBot', 'ExampleV1', 'ExampleV2', 'ExampleV3', 'AntiAIBot'):
    with open(os.path.join(HERE, name + '.pkl'), 'wb') as f:
        pickle.dump(getattr(sparring, name)(), f, protocol=4)
print('wrote', len(os.listdir(HERE)) - 3, 'pickles to', HERE)

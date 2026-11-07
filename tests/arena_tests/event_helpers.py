"""Teams for the event tests: real model files and real pickles."""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

CALL = '''from player_action import PlayerAction


class Model:
    def __init__(self):
        self.player_id = None

    def seed(self, hand, player_id):
        self.player_id = player_id

    def run(self, actions, game_state):
        return PlayerAction.call(self.player_id)
'''

FOLD = CALL.replace(
    "return PlayerAction.call(self.player_id)",
    "return (PlayerAction.fold(self.player_id) if game_state.call_amount\n"
    "                else PlayerAction.call(self.player_id))",
)

SHOVE = CALL.replace(
    "return PlayerAction.call(self.player_id)",
    "if game_state.can_raise:\n"
    "            return PlayerAction.raise_to(\n"
    "                self.player_id, game_state.max_raise_to)\n"
    "        return PlayerAction.call(self.player_id)",
)

CRASH = CALL.replace("return PlayerAction.call(self.player_id)",
                     'raise RuntimeError("boom")')

HELPER_MODEL = '''from helper import strong
from player_action import PlayerAction


class Model:
    def __init__(self):
        self.hand = []
        self.player_id = None

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, game_state):
        if strong(self.hand) and game_state.can_raise:
            return PlayerAction.raise_to(self.player_id,
                                         game_state.min_raise_to)
        return PlayerAction.call(self.player_id)
'''
HELPER = ("def strong(hand):\n"
          "    return sorted(c.value for c in hand)[0] >= 10\n")

HOSTILE_PICKLE = '''import os, pickle


class Boom:
    def __reduce__(self):
        return (os.system, ("echo pwned > pwned.txt",))


pickle.dump(Boom(), open(r"{path}", "wb"))
'''


def make_team(folder, files: dict, pickle_as="team"):
    """Writes files (name -> text) into folder and pickles its Model to
    folder/<pickle_as>.pkl in a clean process. Returns the .pkl path."""
    os.makedirs(folder, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)
    path = os.path.join(folder, pickle_as + ".pkl")
    code = ("import pickle, model; "
            f"pickle.dump(model.Model(), open(r'{path}', 'wb'))")
    env = dict(os.environ, PYTHONPATH=REPO, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-c", code], cwd=folder, env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return path


def make_hostile(path):
    code = HOSTILE_PICKLE.format(path=path)
    done = subprocess.run([sys.executable, "-c", code],
                          cwd=os.path.dirname(path), capture_output=True,
                          text=True)
    assert done.returncode == 0, done.stderr
    return path

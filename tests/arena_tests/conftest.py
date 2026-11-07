"""Arena tests: shared fixtures (test bots, a fake worker, dummies)."""

import os
import pickle
import sys
import textwrap

import pytest

# The repo root: workers run from here.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

BOT_MODULE = "arena_hidden_bots"

BOT_SOURCE = r'''
import os
import sys
import time

from player_action import PlayerAction


def _note_pid(obj):
    path = obj.__dict__.get("pid_file")
    if path:
        with open(path, "a") as f:
            f.write(f"{os.getpid()}\n")


class Normal:
    def __init__(self, pid_file=None):
        self.pid_file = pid_file
        self.seeds = 0
        self.runs = 0
        self.ends = 0
        self.pid = None

    def __setstate__(self, state):
        self.__dict__.update(state)
        _note_pid(self)

    def seed(self, hand, player_id):
        self.seeds += 1
        self.pid = player_id

    def run(self, actions, game_state):
        self.runs += 1
        return PlayerAction.call(self.pid)

    def end_game(self, actions, game_state):
        self.ends += 1


class NoEnd(Normal):
    end_game = None


class StateEcho(Normal):
    def run(self, actions, game_state):
        raise ValueError(
            f"{game_state.pot}|{game_state.to_act}|{len(actions)}|"
            f"{len(game_state.board)}|{type(game_state).__name__}")


class SeedRaise(Normal):
    def seed(self, hand, player_id):
        raise ValueError("seed boom")


class RunRaise(Normal):
    def run(self, actions, game_state):
        raise KeyError("run boom")


class EndRaise(Normal):
    def end_game(self, actions, game_state):
        raise ValueError("end boom")


class NastyExc(Exception):
    def __str__(self):
        raise RuntimeError("no str")


class NastyStr(Normal):
    def run(self, actions, game_state):
        raise NastyExc()


class ReturnsNone(Normal):
    def run(self, actions, game_state):
        return None


class ReturnsStr(Normal):
    def run(self, actions, game_state):
        return "call"


class Printer(Normal):
    def __setstate__(self, state):
        print("printed while unpickling")
        os.write(1, b"raw fd1 while unpickling\n")
        super().__setstate__(state)

    def seed(self, hand, player_id):
        print("PRINTED-IN-SEED")
        sys.stdout.flush()
        super().seed(hand, player_id)

    def run(self, actions, game_state):
        print("printed in run", flush=True)
        os.write(1, b"\x00\x00\x00\x05garbage-on-fd-1")
        return super().run(actions, game_state)


class Sleeper(Normal):
    def run(self, actions, game_state):
        time.sleep(30)
        return super().run(actions, game_state)


class SlowUnpickle(Normal):
    def __setstate__(self, state):
        time.sleep(30)
        super().__setstate__(state)


class Exiter(Normal):
    def run(self, actions, game_state):
        os._exit(3)


class HugeError(Normal):
    def run(self, actions, game_state):
        raise ValueError("x" * 2_000_000)


class BigState(Normal):
    def __init__(self, pid_file=None):
        super().__init__(pid_file)
        self.blob = b"y" * 5_000_000


class NoCheckpoint(Normal):
    def __getstate__(self):
        if self.__dict__.get("armed"):
            raise TypeError("cannot pickle me")
        return dict(self.__dict__)

    def __setstate__(self, state):
        super().__setstate__(state)
        self.armed = True


class MemHog(Normal):
    def run(self, actions, game_state):
        hog = bytearray(2 * 1024 ** 3)
        return PlayerAction.call(self.pid + len(hog) * 0)
'''

FAKE_WORKER = r'''
import json
import os
import pickle
import struct
import sys
import time

mode = sys.argv[1]
inp = sys.stdin.buffer
out = sys.stdout.buffer
MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MARKER")


class Evil:
    def __reduce__(self):
        return (open, (MARKER, "w"))


def read():
    head = inp.read(4)
    if len(head) < 4:
        sys.exit(0)
    return inp.read(struct.unpack(">I", head)[0])


def send(payload):
    out.write(struct.pack(">I", len(payload)) + payload)
    out.flush()


def action(**fields):
    send(json.dumps({"action": fields}).encode())


if mode == "sleepstart":
    time.sleep(30)
while True:
    read()
    if mode == "nonjson":
        send(b"\xff\xfe not json")
    elif mode == "notobject":
        send(b"[1, 2, 3]")
    elif mode == "toolarge":
        out.write(struct.pack(">I", 2 * 1024 * 1024) + b"{")
        out.flush()
        time.sleep(30)
    elif mode == "hugecheckpoint":
        out.write(struct.pack(">I", 101 * 1024 * 1024) + b"{")
        out.flush()
        time.sleep(30)
    elif mode == "truncated":
        out.write(struct.pack(">I", 100) + b"abc")
        out.flush()
        sys.exit(0)
    elif mode == "wrongshape":
        send(json.dumps({"action": "call"}).encode())
    elif mode == "missingkey":
        send(json.dumps({"action": {"player_id": 0, "kind": "call"}}).encode())
    elif mode == "badkind":
        action(player_id=0, kind="bogus", amount=0, card=None)
    elif mode == "badcard":
        action(player_id=-1, kind="reveal_card", amount=0, card="ZZ")
    elif mode == "badtype":
        action(player_id="0", kind="call", amount=0, card=None)
    elif mode == "goodcard":
        action(player_id=-1, kind="reveal_card", amount=0, card="AS")
    elif mode == "pickle":
        send(pickle.dumps(Evil()))
    elif mode == "error":
        send(json.dumps({"error": "ValueError: from fake"}).encode())
    else:
        send(json.dumps({"ok": None}).encode())
'''

LOGGER = r'''
import subprocess
import sys
with open(sys.argv[1], "a") as f:
    f.write("\t".join(sys.argv[2:]) + "\n")
# (os.execvp splits arguments at spaces on Windows.)
sys.exit(subprocess.call(sys.argv[2:]))
'''


@pytest.fixture(scope="session")
def bots_dir(tmp_path_factory):
    """A folder with arena_hidden_bots.py and one .pkl per class."""
    folder = tmp_path_factory.mktemp("bots")
    (folder / f"{BOT_MODULE}.py").write_text(BOT_SOURCE)
    sys.path.insert(0, str(folder))
    import importlib
    mod = importlib.import_module(BOT_MODULE)
    for name in ("Normal", "NoEnd", "StateEcho", "SeedRaise", "RunRaise",
                 "EndRaise", "NastyStr", "ReturnsNone", "ReturnsStr",
                 "Printer", "Sleeper", "SlowUnpickle", "Exiter", "HugeError",
                 "BigState", "NoCheckpoint", "MemHog"):
        obj = getattr(mod, name)(pid_file=str(folder / "pids.txt"))
        (folder / f"{name}.pkl").write_bytes(pickle.dumps(obj))
    (folder / "garbage.pkl").write_bytes(os.urandom(200))
    ghost = tmp_path_factory.mktemp("ghost")
    (ghost / "ghost_mod_hidden.py").write_text(
        "class Ghost:\n    pass\n")
    sys.path.insert(0, str(ghost))
    gm = importlib.import_module("ghost_mod_hidden")
    (folder / "missing.pkl").write_bytes(pickle.dumps(gm.Ghost()))
    sys.path.remove(str(ghost))
    return folder


@pytest.fixture(scope="session")
def tools_dir(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tools")
    (folder / "fake_worker.py").write_text(FAKE_WORKER)
    (folder / "logger.py").write_text(LOGGER)
    return folder


@pytest.fixture
def fake(tools_dir):
    """launcher tuple for a fake worker in the given mode."""
    marker = tools_dir / "MARKER"
    if marker.exists():
        marker.unlink()

    def launcher(mode):
        return (sys.executable, str(tools_dir / "fake_worker.py"), mode)
    launcher.marker = marker
    return launcher


@pytest.fixture
def dummies(monkeypatch):
    """install(name=BaseClass, ...) -> list of Players; sets DUMMIES."""
    import arena.dummies
    import arena_helpers as H

    def install(**bases):
        H.reset()
        table = {name: H.named(name, base) for name, base in bases.items()}
        monkeypatch.setattr(arena.dummies, "DUMMIES", table)
        from arena.players import Player
        return [Player(name, "dummy:" + name) for name in bases]
    return install

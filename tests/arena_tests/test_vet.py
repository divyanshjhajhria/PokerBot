"""Vetting: what a pickle may name, and what the code is flagged for."""

import os
import pickle
import subprocess
import sys

import pytest

import event_helpers as E
from arena import vet


def write(path, text):
    with open(path, "w", encoding="utf-8") as file:
        file.write(text)
    return str(path)


def pickle_of(path, obj, protocol=4):
    with open(path, "wb") as file:
        pickle.dump(obj, file, protocol=protocol)
    return str(path)


class Calls:
    def __init__(self, func, args):
        self.func, self.args = func, args

    def __reduce__(self):
        return (self.func, self.args)


# -- the pickle ---------------------------------------------------------


def test_a_real_model_pickle_names_only_its_own_module(tmp_path):
    pkl = E.make_team(str(tmp_path), {"model.py": E.CALL})
    names, bad = vet.scan_pickle(pkl, own_modules={"model"})
    assert names == ["model.Model"]
    assert bad == []


def test_a_model_with_plain_data_is_clean(tmp_path):
    source = E.CALL.replace(
        "self.player_id = None",
        "self.player_id = None\n        self.table = {1: [2.5, (3, 4)],"
        " 'a': {1, 2}, 'b': frozenset([1]), 'c': b'x', 'd': 1j}\n"
        "        import collections\n"
        "        self.q = collections.deque([1])\n"
        "        self.c = collections.Counter('abc')\n"
        "        self.o = collections.OrderedDict(a=1)",
    )
    pkl = E.make_team(str(tmp_path), {"model.py": source})
    _, bad = vet.scan_pickle(pkl, own_modules={"model"})
    assert bad == []


@pytest.mark.parametrize("protocol", [0, 1, 2, 4, 5])
def test_calling_os_system_is_refused_in_every_protocol(tmp_path, protocol):
    pkl = pickle_of(tmp_path / "x.pkl", Calls(os.system, ("echo hi",)),
                    protocol)
    _, bad = vet.scan_pickle(pkl)
    assert bad and bad[0].endswith(".system")


@pytest.mark.parametrize("func", [eval, exec, getattr, __import__, open,
                                  subprocess.run, compile])
def test_dangerous_functions_are_refused(tmp_path, func):
    pkl = pickle_of(tmp_path / "x.pkl", Calls(func, ("x",)))
    _, bad = vet.scan_pickle(pkl)
    assert bad, func


def test_nothing_in_a_hostile_pickle_runs(tmp_path):
    pkl = E.make_hostile(str(tmp_path / "evil.pkl"))
    _, bad = vet.scan_pickle(pkl)
    assert bad
    # The hostile pickle would write pwned.txt where it was read.
    assert not (tmp_path / "pwned.txt").exists()
    assert not os.path.exists(os.path.join(E.REPO, "pwned.txt"))


def test_a_module_the_team_did_not_send_is_refused(tmp_path):
    pkl = E.make_team(str(tmp_path), {"model.py": E.CALL})
    _, bad = vet.scan_pickle(pkl, own_modules={"other"})
    assert bad == ["model.Model"]


def test_allow_lets_an_organiser_admit_a_library(tmp_path):
    pkl = pickle_of(tmp_path / "x.pkl", Calls(subprocess.run, ("x",)))
    _, bad = vet.scan_pickle(pkl, allow=["subprocess"])
    assert bad == []


def test_a_pickle_bomb_or_garbage_is_refused_not_hung(tmp_path):
    junk = tmp_path / "junk.pkl"
    junk.write_bytes(b"not a pickle at all")
    _, bad = vet.scan_pickle(str(junk))
    assert bad
    deep = tmp_path / "deep.pkl"
    deep.write_bytes(b"(" * 200000 + b"." )
    _, bad = vet.scan_pickle(str(deep), timeout=20)
    assert bad


def test_a_huge_file_is_refused_unread(tmp_path, monkeypatch):
    path = tmp_path / "big.pkl"
    path.write_bytes(b"0" * 100)
    monkeypatch.setattr(vet, "MAX_PICKLE", 10)
    _, bad = vet.scan_pickle(str(path))
    assert bad and "MB" in bad[0]


# -- the source ---------------------------------------------------------


def findings(tmp_path, source, own=()):
    path = write(tmp_path / "model.py", source)
    return vet.scan_source(path, "model.py", own)


def messages(found):
    return " | ".join(f.message for f in found)


@pytest.mark.parametrize("source, expect", [
    ("import os\n", "imports os"),
    ("import subprocess\n", "imports subprocess"),
    ("from socket import socket\n", "imports socket"),
    ("import requests\n", "imports requests"),
    ("eval('1')\n", "calls eval()"),
    ("exec('x=1')\n", "calls exec()"),
    ("open('f')\n", "calls open()"),
    ("__import__('os')\n", "calls __import__()"),
    ("x = ().__class__\n", "reaches into .__class__"),
    ("def f(g):\n    return g._state\n", "reaches into ._state"),
    ("import sys\nsys._getframe()\n", "imports sys"),
    ("class A:\n    def __reduce__(self):\n        return (id, ())\n",
     "defines __reduce__"),
    ("class A:\n    def __setstate__(self, s):\n        pass\n",
     "defines __setstate__"),
    ("x = 'http://evil.example/x'\n", "mentions"),
    ("getattr(x, 'y')\n", "calls getattr()"),
])
def test_what_the_code_review_flags(tmp_path, source, expect):
    assert expect in messages(findings(tmp_path, source))


def test_plain_code_is_not_flagged(tmp_path):
    source = (
        "import math\nimport random\nfrom collections import Counter\n"
        "import numpy as np\nfrom card_utils import Card, evaluate\n"
        "from player_action import PlayerAction\n\n"
        "class Model:\n    def run(self, a, s):\n"
        "        return PlayerAction.call(0)\n"
    )
    assert findings(tmp_path, source) == []


def test_importing_the_teams_own_files_is_fine(tmp_path):
    assert findings(tmp_path, "import helper\nfrom tables import T\n") != []
    assert findings(tmp_path, "import helper\nfrom tables import T\n",
                    own={"helper", "tables"}) == []


def test_code_that_does_not_parse_is_flagged(tmp_path):
    found = findings(tmp_path, "def broken(:\n")
    assert found and found[0].severity == "high"


# -- a whole submission -------------------------------------------------


def test_a_clean_submission_is_ok(tmp_path):
    pkl = E.make_team(str(tmp_path), {"model.py": E.CALL})
    report = vet.vet(pkl, [str(tmp_path / "model.py")])
    assert report.ok and not report.lines()


def test_a_submission_with_a_helper_file(tmp_path):
    pkl = E.make_team(str(tmp_path), {"model.py": E.HELPER_MODEL,
                                      "helper.py": E.HELPER})
    report = vet.vet(pkl, [str(tmp_path / "model.py"),
                           str(tmp_path / "helper.py")])
    assert report.ok, report.lines()


def test_flagged_code_needs_review_but_is_not_refused(tmp_path):
    pkl = E.make_team(str(tmp_path), {
        "model.py": "import os\n" + E.CALL})
    report = vet.vet(pkl, [str(tmp_path / "model.py")])
    assert report.needs_review and not report.refused and not report.ok


def test_a_hostile_pickle_is_refused(tmp_path):
    pkl = E.make_hostile(str(tmp_path / "team.pkl"))
    write(tmp_path / "model.py", E.CALL)
    report = vet.vet(pkl, [str(tmp_path / "model.py")])
    assert report.refused and report.violations


@pytest.mark.parametrize("name", ["random.py", "os.py", "engine.py",
                                  "card_utils.py", "my-bot.py", "a b.py",
                                  "9lives.py"])
def test_bad_file_names_are_refused(tmp_path, name):
    pkl = E.make_team(str(tmp_path), {"model.py": E.CALL})
    path = write(tmp_path / name, "x = 1\n")
    report = vet.vet(pkl, [str(tmp_path / "model.py"), path])
    assert report.problems, name


def test_too_many_or_too_big_files_are_refused(tmp_path, monkeypatch):
    pkl = E.make_team(str(tmp_path), {"model.py": E.CALL})
    sources = [str(tmp_path / "model.py")]
    for i in range(3):
        sources.append(write(tmp_path / f"m{i}.py", "x = 1\n"))
    monkeypatch.setattr(vet, "MAX_FILES", 2)
    assert vet.vet(pkl, sources).problems
    monkeypatch.setattr(vet, "MAX_FILES", 20)
    monkeypatch.setattr(vet, "MAX_SOURCE", 3)
    assert vet.vet(pkl, sources[:1]).problems


def test_a_class_defined_in_a_script_gets_a_helpful_message(tmp_path):
    code = ("import pickle\n"
            "class Model:\n    pass\n"
            "pickle.dump(Model(), open('team.pkl', 'wb'))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    write(tmp_path / "model.py", E.CALL)
    report = vet.vet(str(tmp_path / "team.pkl"), [str(tmp_path / "model.py")])
    assert report.refused
    assert "script you ran directly" in " ".join(report.lines())


def test_the_scan_child_process_prints_json():
    # The child that scan_pickle runs: python -m arena.vet FILE.
    done = subprocess.run([sys.executable, "-m", "arena.vet", "missing.pkl"],
                          cwd=E.REPO, capture_output=True, text=True)
    assert done.returncode == 1 and "error" in done.stdout

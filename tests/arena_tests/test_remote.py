import base64
import json
import os
import pickle
import struct
import subprocess
import sys
import time

import pytest

import arena_helpers as H
from conftest import REPO
from arena.remote import RemoteError, RemoteModel
from card_utils import Card
from game_state import GameState, GameStateView
from player_action import PlayerAction as A

HAND = [Card.parse("AS"), Card.parse("AH")]
FAST = 10.0  # generous timeout for calls that should answer quickly
SLOW = 2.0  # timeout for tests that expect a timeout


def view(stacks=(500, 500)):
    return GameStateView(GameState(list(stacks)))


def rm(bots_dir, name, timeout=FAST, **kw):
    return RemoteModel(str(bots_dir / f"{name}.pkl"), timeout, **kw)


def worker_pid(bots_dir, before):
    pids = H.read_pids(bots_dir)
    new = pids[len(before):]
    assert new, "worker did not record its pid"
    return new[-1]


# -- raw worker protocol -------------------------------------------------------

def _frame(obj):
    data = pickle.dumps(obj)
    return struct.pack(">I", len(data)) + data


def _reply(proc):
    head = proc.stdout.read(4)
    assert len(head) == 4
    return json.loads(proc.stdout.read(struct.unpack(">I", head)[0]))


def _spawn(pkl):
    return subprocess.Popen(
        [sys.executable, "-m", "arena.worker", str(pkl), "1024"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=REPO)


def test_RM01_worker_protocol(bots_dir):
    """RM-01: the worker speaks length-prefixed frames: pickled (command, args) in, JSON out ({"ok": null}, {"action": ...}, {"pickle": base64}, {"error": "Type: msg"}); close and EOF exit 0."""
    proc = _spawn(bots_dir / "Normal.pkl")
    try:
        state = GameState([500, 500])
        proc.stdin.write(_frame(("seed", (HAND, 0))))
        proc.stdin.flush()
        assert _reply(proc) == {"ok": None}
        proc.stdin.write(_frame(("run", ([], state))))
        proc.stdin.flush()
        assert _reply(proc) == {"action": {"player_id": 0, "kind": "call",
                                           "amount": 0, "card": None}}
        proc.stdin.write(_frame(("end_game", ([], state))))
        proc.stdin.flush()
        assert _reply(proc) == {"ok": None}
        proc.stdin.write(_frame(("checkpoint", ())))
        proc.stdin.flush()
        model = pickle.loads(base64.b64decode(_reply(proc)["pickle"]))
        assert (model.seeds, model.runs, model.ends) == (1, 1, 1)
        proc.stdin.write(_frame(("close", ())))
        proc.stdin.flush()
        assert _reply(proc) == {"ok": None}
        assert proc.wait(5) == 0
    finally:
        proc.kill()
    proc = _spawn(bots_dir / "RunRaise.pkl")
    try:
        proc.stdin.write(_frame(("run", ([], GameState([500, 500])))))
        proc.stdin.flush()
        assert _reply(proc) == {"error": "KeyError: 'run boom'"}
        proc.stdin.close()
        assert proc.wait(5) == 0
    finally:
        proc.kill()


# -- RemoteModel ---------------------------------------------------------------

def test_RM02_run_returns_playeraction_and_sees_state(bots_dir):
    """RM-02: run returns a real PlayerAction; the worker sees the snapshot state (pot, to_act, board) as a GameStateView and the actions."""
    m = rm(bots_dir, "Normal")
    try:
        m.seed(HAND, 0)
        act = m.run([], view())
        assert type(act) is A and act == A.call(0)
    finally:
        m.close()
    m = rm(bots_dir, "StateEcho")
    try:
        state = GameState([500, 500])
        state.process_action(A.call(0))
        with pytest.raises(RemoteError) as e:
            m.run([A.call(0)], GameStateView(state))
        assert str(e.value) == "ValueError: 40|1|1|0|GameStateView"
    finally:
        m.close()


@pytest.mark.parametrize("name, call, text", [
    ("SeedRaise", "seed", "ValueError: seed boom"),
    ("RunRaise", "run", "KeyError: 'run boom'"),
    ("EndRaise", "end_game", "ValueError: end boom"),
])
def test_RM03_model_exceptions(bots_dir, name, call, text):
    """RM-03: exceptions in seed, run or end_game raise RemoteError("<Type>: <message>") and the worker keeps serving."""
    m = rm(bots_dir, name)
    try:
        args = {"seed": (HAND, 0), "run": ([], view()),
                "end_game": ([], view())}[call]
        with pytest.raises(RemoteError) as e:
            getattr(m, call)(*args)
        assert str(e.value) == text
        assert isinstance(m.checkpoint(), bytes)  # still serving
        with pytest.raises(RemoteError):
            getattr(m, call)(*args)
    finally:
        m.close()


def test_RM04_undescribable_exception(bots_dir):
    """RM-04: an exception whose str() raises still gives a RemoteError with a non-empty text starting with its type name."""
    m = rm(bots_dir, "NastyStr")
    try:
        with pytest.raises(RemoteError) as e:
            m.run([], view())
        assert str(e.value).startswith("NastyExc")
        assert isinstance(m.checkpoint(), bytes)
    finally:
        m.close()


@pytest.mark.parametrize("name", ["ReturnsNone", "ReturnsStr"])
def test_RM05_non_playeraction(bots_dir, name):
    """RM-05: run returning a non-PlayerAction raises RemoteError and the worker keeps serving."""
    m = rm(bots_dir, name)
    try:
        with pytest.raises(RemoteError):
            m.run([], view())
        assert isinstance(m.checkpoint(), bytes)
    finally:
        m.close()


def test_RM06_prints_do_not_break_protocol(bots_dir, capfd):
    """RM-06: print and raw writes to fd 1 in the model (also while unpickling) never corrupt the protocol; prints reach stderr."""
    m = rm(bots_dir, "Printer")
    try:
        m.seed(HAND, 0)
        assert m.run([], view()) == A.call(0)
        assert m.run([], view()) == A.call(0)
        m.end_game([], view())
    finally:
        m.close()
    assert "PRINTED-IN-SEED" in capfd.readouterr().err


def test_RM07_timeout_kills(bots_dir):
    """RM-07: a call over the timeout raises TimeoutError promptly, kills the worker, and later calls raise RemoteError."""
    before = H.read_pids(bots_dir)
    m = rm(bots_dir, "Sleeper", timeout=SLOW)
    try:
        m.seed(HAND, 0)
        pid = worker_pid(bots_dir, before)
        start = time.time()
        with pytest.raises(TimeoutError):
            m.run([], view())
        assert time.time() - start < SLOW + 3
        assert H.wait_dead(pid)
        start = time.time()
        with pytest.raises(RemoteError):
            m.seed(HAND, 0)
        assert time.time() - start < 1
    finally:
        m.close()


def test_RM08_startup_counts_against_timeout(bots_dir, fake):
    """RM-08: slow start-up (slow unpickling, slow launcher) makes the first call raise TimeoutError."""
    for m in (rm(bots_dir, "SlowUnpickle", timeout=SLOW),
              rm(bots_dir, "Normal", timeout=SLOW,
                 launcher=fake("sleepstart"))):
        try:
            start = time.time()
            with pytest.raises(TimeoutError):
                m.seed(HAND, 0)
            assert time.time() - start < SLOW + 3
            with pytest.raises(RemoteError):
                m.seed(HAND, 0)
        finally:
            m.close()


def test_RM09_dead_worker(bots_dir):
    """RM-09: a worker that exits mid-call raises RemoteError, and so does every later call (no restart)."""
    m = rm(bots_dir, "Exiter")
    try:
        m.seed(HAND, 0)
        with pytest.raises(RemoteError):
            m.run([], view())
        for _ in range(2):
            with pytest.raises(RemoteError):
                m.seed(HAND, 0)
    finally:
        m.close()


def test_RM10_reply_limits(bots_dir, fake):
    """RM-10: replies over 1 MiB are a RemoteError without waiting for the body; checkpoints over 1 MiB are fine but over 100 MiB are a RemoteError."""
    m = rm(bots_dir, "HugeError")
    try:
        with pytest.raises(RemoteError):
            m.run([], view())
    finally:
        m.close()
    m = rm(bots_dir, "Normal", launcher=fake("toolarge"))
    try:
        start = time.time()
        with pytest.raises(RemoteError):
            m.run([], view())
        assert time.time() - start < 5
    finally:
        m.close()
    m = rm(bots_dir, "BigState")
    try:
        blob = m.checkpoint()
        assert len(blob) > 5_000_000
        assert len(pickle.loads(blob).blob) == 5_000_000
    finally:
        m.close()
    m = rm(bots_dir, "Normal", launcher=fake("hugecheckpoint"))
    try:
        start = time.time()
        with pytest.raises(RemoteError):
            m.checkpoint()
        assert time.time() - start < 5
    finally:
        m.close()


@pytest.mark.parametrize("mode", ["nonjson", "notobject", "truncated",
                                  "wrongshape", "missingkey", "pickle"])
def test_RM11_malformed_replies(bots_dir, fake, mode):
    """RM-11: malformed worker replies (non-JSON, not an object, truncated, wrong shape, missing keys, a pickle) raise RemoteError; nothing sent by the worker is ever unpickled."""
    m = rm(bots_dir, "Normal", launcher=fake(mode))
    try:
        with pytest.raises(RemoteError):
            m.run([], view())
    finally:
        m.close()
    assert not fake.marker.exists()


@pytest.mark.parametrize("mode", ["badkind", "badcard", "badtype"])
def test_RM12_invalid_action_values(bots_dir, fake, mode):
    """RM-12: a well-shaped run reply with invalid values raises the PlayerAction/Card construction error (a ValueError), not RemoteError."""
    m = rm(bots_dir, "Normal", launcher=fake(mode))
    try:
        with pytest.raises(ValueError) as e:
            m.run([], view())
        assert not isinstance(e.value, RemoteError)
    finally:
        m.close()


def test_RM12b_card_parsed(bots_dir, fake):
    """RM-12: a reply's card string is parsed into a Card."""
    m = rm(bots_dir, "Normal", launcher=fake("goodcard"))
    try:
        assert m.run([], view()) == A.reveal_card(Card.parse("AS"))
    finally:
        m.close()


@pytest.mark.parametrize("name", ["garbage", "missing"])
def test_RM13_broken_pickle(bots_dir, name):
    """RM-13: a .pkl that cannot be unpickled raises RemoteError on the first call and on every later call."""
    m = rm(bots_dir, name)
    try:
        with pytest.raises(RemoteError):
            m.seed(HAND, 0)
        with pytest.raises(RemoteError):
            m.seed(HAND, 0)
    finally:
        m.close()


def test_RM14_sys_path_and_cwd(bots_dir, monkeypatch, tmp_path):
    """RM-14: the model's module next to the .pkl imports, whatever the caller's working directory."""
    monkeypatch.chdir(tmp_path)
    m = rm(bots_dir, "Normal")
    try:
        m.seed(HAND, 1)
        assert m.run([], view()) == A.call(1)
    finally:
        m.close()


def test_RM15_end_game_always_exists(bots_dir):
    """RM-15: RemoteModel.end_game exists and returns None for a model without a callable end_game."""
    m = rm(bots_dir, "NoEnd")
    try:
        assert callable(m.end_game)
        assert m.end_game([], view()) is None
    finally:
        m.close()


def test_RM16_checkpoint_round_trip(bots_dir):
    """RM-16: checkpoint() returns bytes that unpickle to the model's current state; a model that cannot be pickled gives RemoteError."""
    m = rm(bots_dir, "Normal")
    try:
        m.seed(HAND, 0)
        m.run([], view())
        m.run([], view())
        m.end_game([], view())
        model = pickle.loads(m.checkpoint())
        assert (model.seeds, model.runs, model.ends) == (1, 2, 1)
    finally:
        m.close()
    m = rm(bots_dir, "NoCheckpoint")
    try:
        m.seed(HAND, 0)
        with pytest.raises(RemoteError):
            m.checkpoint()
    finally:
        m.close()


def test_RM17_close(bots_dir):
    """RM-17: close never raises (twice, before start, after a kill or crash), the worker is gone afterwards, and later calls raise RemoteError."""
    RemoteModel(str(bots_dir / "Normal.pkl"), FAST).close()
    before = H.read_pids(bots_dir)
    m = rm(bots_dir, "Normal")
    m.seed(HAND, 0)
    pid = worker_pid(bots_dir, before)
    m.close()
    m.close()
    assert H.wait_dead(pid, 2.5)
    with pytest.raises(RemoteError):
        m.seed(HAND, 0)
    m.close()
    m = rm(bots_dir, "Exiter")
    m.seed(HAND, 0)
    with pytest.raises(RemoteError):
        m.run([], view())
    m.close()
    m.close()


def test_RM18_not_picklable(bots_dir):
    """RM-18: a RemoteModel cannot be pickled."""
    m = rm(bots_dir, "Normal")
    try:
        with pytest.raises(Exception):
            pickle.dumps(m)
    finally:
        m.close()


def test_RM19_launcher_and_arguments(bots_dir, tools_dir, tmp_path,
                                     monkeypatch):
    """RM-19: the launcher prefix runs the worker as [*launcher, python, -m, arena.worker, <absolute pkl>, <memory_mb>]."""
    log = tmp_path / "argv.txt"
    launcher = (sys.executable, str(tools_dir / "logger.py"), str(log))
    monkeypatch.chdir(bots_dir)
    m = RemoteModel("Normal.pkl", FAST, launcher, 777)
    try:
        m.seed(HAND, 0)
    finally:
        m.close()
    argv = log.read_text().strip().split("\t")
    assert argv[-4:] == ["-m", "arena.worker",
                         str(bots_dir / "Normal.pkl"), "777"]
    assert os.path.isabs(argv[-2])


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="RLIMIT_AS is only checked on Linux")
def test_RM20_memory_limit(bots_dir):
    """RM-20: a model allocating far over memory_mb fails cleanly with RemoteError; a limit too small to start raises an exception and never hangs."""
    m = rm(bots_dir, "MemHog", memory_mb=512)
    try:
        m.seed(HAND, 0)
        with pytest.raises(RemoteError):
            m.run([], view())
    finally:
        m.close()
    m = rm(bots_dir, "Normal", timeout=5, memory_mb=1)
    try:
        start = time.time()
        with pytest.raises((RemoteError, TimeoutError)):
            m.seed(HAND, 0)
        assert time.time() - start < 7
    finally:
        m.close()


def test_RM21_remote_models_in_engine(bots_dir):
    """RM-21: play_hand with RemoteModels works: raising remote models become faults, net sums to 0, end_game reaches the worker."""
    from card_utils import full_deck
    from engine import play_hand

    models = [rm(bots_dir, "Normal"), rm(bots_dir, "RunRaise"),
              rm(bots_dir, "SeedRaise")]
    try:
        result = play_hand(models, full_deck(), [500, 500, 500])
        assert set(result.faults) == {1, 2}
        assert sum(result.net) == 0
        state = pickle.loads(models[0].checkpoint())
        assert state.seeds == 1 and state.ends == 1
    finally:
        for m in models:
            m.close()

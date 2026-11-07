import base64
import json
import os
import pickle
import queue
import signal
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Sequence

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction

TIME_LIMIT = 10.0  # seconds per call, until the organisers decide

MAX_REPLY = 1 << 20  # bytes in any reply but a checkpoint
MAX_CHECKPOINT = 100 << 20  # bytes in a checkpoint reply

# The folder holding the arena package: the worker runs from there.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The fields of a run reply's action, all required.
_ACTION_KEYS = {"player_id", "kind", "amount", "card"}


class RemoteError(RuntimeError):
    """The model in the worker raised, or the worker misbehaved."""


class RemoteModel:
    """
    A model running in its own worker process, unpickled there from
    pkl_path. Has the model protocol (seed, run, end_game, close), so the
    engine treats it like any other model.

    The worker is untrusted: its replies are length-prefixed UTF-8 JSON,
    never pickles, and are size-checked before they are read. A call that
    takes longer than timeout seconds (start-up included) kills the worker
    and raises TimeoutError. Once the worker is gone, for any reason, every
    call raises RemoteError: it is never restarted.
    """

    def __init__(
        self,
        pkl_path: str,
        timeout: float = TIME_LIMIT,
        launcher: Sequence[str] = (),
        memory_mb: int = 2048,
    ):
        self.pkl_path = os.path.abspath(pkl_path)
        self.timeout = timeout
        self.launcher = list(launcher)
        self.memory_mb = memory_mb
        self._process = None  # started by the first call
        self._dead = False  # set for good once the worker is gone
        self._replies = None  # frames the reader thread has read
        self._limit = [MAX_REPLY]  # largest reply the reader accepts

    def __reduce__(self):
        raise TypeError("a RemoteModel cannot be pickled")

    # -- the model protocol --------------------------------------------------

    def seed(self, hand, player_id) -> None:
        self._expect(self._call("seed", (hand, player_id)), "ok")

    def run(self, actions: list[PlayerAction],
            game_state: GameStateView) -> PlayerAction:
        reply = self._call("run", (actions, _snapshot(game_state)))
        action = self._expect(reply, "action")
        if not isinstance(action, dict) or set(action) != _ACTION_KEYS:
            self._kill()
            raise RemoteError("malformed action from the worker")
        # Wrong values are the model's fault: the error propagates as is.
        card = action["card"]
        return PlayerAction(
            action["player_id"],
            action["kind"],
            action["amount"],
            card=Card.parse(card) if card else None,
        )

    def end_game(self, actions: list[PlayerAction],
                 game_state: GameStateView) -> None:
        reply = self._call("end_game", (actions, _snapshot(game_state)))
        self._expect(reply, "ok")

    def checkpoint(self) -> bytes:
        """The model as it is now, pickled by the worker."""
        reply = self._call("checkpoint", (), MAX_CHECKPOINT)
        data = self._expect(reply, "pickle")
        try:
            return base64.b64decode(data, validate=True)
        except (TypeError, ValueError):
            self._kill()
            raise RemoteError("malformed checkpoint from the worker") from None

    def close(self) -> None:
        """Asks the worker to exit, waits up to 1 s, then kills it. Never
        raises; later calls raise RemoteError."""
        try:
            process, self._dead = self._process, True
            if process is None or process.poll() is not None:
                self._kill()
                return
            try:
                frame = _frame(pickle.dumps(("close", ())))
                _write(process, frame, time.monotonic() + 1)
                process.wait(1)
            except Exception:
                pass
            self._kill()
        except Exception:
            pass

    def __del__(self):
        # Best effort: a half-built object may lack the attributes.
        if "_dead" in self.__dict__:
            self.close()

    # -- talking to the worker ---------------------------------------------

    def _call(self, command, args, limit=MAX_REPLY) -> dict:
        """
        Sends one request and returns the worker's JSON object. Raises
        RemoteError for a model error (the worker lives on) or a dead or
        misbehaving worker (killed), TimeoutError past the deadline.
        """
        if self._dead:
            raise RemoteError("the worker is not running")
        deadline = time.monotonic() + self.timeout
        try:
            if self._process is None:
                self._start()
            frame = _frame(pickle.dumps((command, args)))
            self._limit[0] = limit
            _write(self._process, frame, deadline)
            try:
                kind, value = self._replies.get(
                    timeout=max(0.0, deadline - time.monotonic())
                )
            except queue.Empty:
                raise TimeoutError from None
            if kind == "eof":
                raise RemoteError("the worker exited")
            if kind == "big":
                raise RemoteError(f"a reply of {value} bytes is too big")
            payload = value
        except TimeoutError:
            self._kill()
            raise TimeoutError(
                f"the worker took more than {self.timeout} s to {command}"
            ) from None
        except RemoteError:
            self._kill()
            raise
        except OSError as error:  # a broken pipe, or the launcher failed
            self._kill()
            raise RemoteError(f"the worker died: {error}") from None
        try:
            reply = json.loads(payload.decode("utf-8"))
        except (ValueError, RecursionError):
            reply = None
        if not isinstance(reply, dict):
            self._kill()
            raise RemoteError("malformed reply from the worker")
        if "error" in reply:
            if not isinstance(reply["error"], str):
                self._kill()
                raise RemoteError("malformed error from the worker")
            raise RemoteError(reply["error"])
        return reply

    def _expect(self, reply: dict, key: str):
        """reply[key], if that is the reply's only key."""
        if set(reply) != {key}:
            self._kill()
            raise RemoteError(f"malformed reply from the worker (no {key!r})")
        return reply[key]

    def _start(self):
        # Its own process group (session on POSIX), so a kill also reaches
        # what the launcher started.
        if os.name == "nt":
            group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
                     | subprocess.CREATE_NO_WINDOW}
        else:
            group = {"start_new_session": True}
        self._process = subprocess.Popen(
            [*self.launcher, sys.executable, "-m", "arena.worker",
             self.pkl_path, str(self.memory_mb)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=_ROOT,
            **group,
        )
        # A thread reads the replies, so waiting for one can time out on
        # every platform (select does not work on pipes on Windows).
        self._replies = queue.Queue()
        threading.Thread(
            target=_pump,
            args=(self._process.stdout, self._replies, self._limit),
            daemon=True,
        ).start()

    def _kill(self):
        """Ends the worker for good, if it ever started."""
        self._dead = True
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            _kill_tree(process)
        process.wait()
        for pipe in (process.stdin, process.stdout):
            try:
                pipe.close()
            except OSError:
                pass


def _snapshot(game_state: GameStateView):
    """The GameState behind the view, to pickle for the worker."""
    return object.__getattribute__(game_state, "_state")


def _frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def _write(process, data: bytes, deadline: float):
    """All of data to the worker's stdin, or TimeoutError past the deadline.

    The write runs in a thread: a worker that is not reading yet (still
    starting up) would otherwise block the caller past its deadline.
    """
    errors: list[BaseException] = []

    def send():
        try:
            process.stdin.write(data)
            process.stdin.flush()
        except (OSError, ValueError) as error:  # a broken or closed pipe
            errors.append(error)

    writer = threading.Thread(target=send, daemon=True)
    writer.start()
    writer.join(max(0.0, deadline - time.monotonic()))
    if writer.is_alive():
        raise TimeoutError
    if errors:
        raise OSError(str(errors[0]))


def _pump(stream, replies: "queue.Queue", limit: list):
    """Reads reply frames from the worker until it ends. Puts ("ok",
    payload), then at the end ("eof", None), or ("big", size) for a reply
    over limit[0] bytes, which is never read."""
    try:
        while True:
            head = stream.read(4)
            if len(head) < 4:
                break
            (size,) = struct.unpack(">I", head)
            if size > limit[0]:
                replies.put(("big", size))
                return
            payload = stream.read(size)
            if len(payload) < size:
                break
            replies.put(("ok", payload))
    except (OSError, ValueError):
        pass
    replies.put(("eof", None))


def _kill_tree(process):
    """Kills the worker and whatever the launcher started with it."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    if process.poll() is None:
        process.kill()

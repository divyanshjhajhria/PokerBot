"""
The worker process: python -m arena.worker PKL_PATH MEMORY_MB

Unpickles one model and answers the parent's requests on stdin/stdout.

Each request is a 4-byte big-endian length, then a pickle of (command,
args) from the trusted parent. Each reply is framed the same way, with a
UTF-8 JSON object as payload: the parent does not trust this process.
"""

import base64
import json
import os
import pickle
import struct
import sys

# Longest model error message replied: an error always fits in a reply.
MAX_MESSAGE = 1000


def main(argv: list[str] | None = None):
    pkl_path, memory_mb = (sys.argv[1:] if argv is None else argv)[:2]
    # Frames use private copies of stdin and stdout. File descriptors 0 and
    # 1, and sys.stdin and sys.stdout, no longer reach the parent, so
    # nothing the model reads or prints touches the protocol.
    requests, replies = os.dup(0), os.dup(1)
    if sys.platform == "win32":
        # Frames are raw bytes: no newline translation, no end-of-file at ^Z.
        import msvcrt

        msvcrt.setmode(requests, os.O_BINARY)
        msvcrt.setmode(replies, os.O_BINARY)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull)
    sys.stdout = sys.stderr
    _limit_memory(int(memory_mb))

    try:
        # The engine's modules first, so a copy shipped with the pickle
        # cannot stand in for them; then modules next to the pickle import.
        import game_state  # noqa: F401
        import player_action  # noqa: F401

        sys.path.insert(0, os.path.dirname(os.path.abspath(pkl_path)))
        with open(pkl_path, "rb") as file:
            model = pickle.load(file)
    except Exception as error:
        if _read_frame(requests) is not None:
            _reply(replies, _encode({"error": _describe(error)}))
        return 1

    while True:
        request = _read_frame(requests)
        if request is None:
            return 0
        command, args = pickle.loads(request)
        if command == "close":
            _reply(replies, _encode({"ok": None}))
            return 0
        try:
            payload = _encode(_dispatch(model, command, args))
        except Exception as error:
            payload = _encode({"error": _describe(error)})
        _reply(replies, payload)


def _dispatch(model, command: str, args) -> dict:
    """Runs one command on the model; its exceptions propagate."""
    # Imported in main, after the memory limit is set.
    from game_state import GameStateView
    from player_action import PlayerAction

    if command == "seed":
        hand, player_id = args
        model.seed(hand, player_id)
        return {"ok": None}
    if command == "run":
        actions, state = args
        action = model.run(actions, GameStateView(state))
        if not isinstance(action, PlayerAction):
            raise TypeError(
                f"run() must return a PlayerAction, not "
                f"{type(action).__name__}"
            )
        card = action.card
        return {"action": {
            "player_id": action.player_id,
            "kind": action.kind,
            "amount": action.amount,
            "card": None if card is None else str(card),
        }}
    if command == "end_game":
        actions, state = args
        end_game = getattr(model, "end_game", None)
        if callable(end_game):
            end_game(actions, GameStateView(state))
        return {"ok": None}
    if command == "checkpoint":
        data = pickle.dumps(model)
        return {"pickle": base64.b64encode(data).decode("ascii")}
    raise ValueError(f"unknown command {command!r}")


def _describe(error: Exception) -> str:
    """"<Type>: <message>"; never raises, even if str() does."""
    try:
        message = str(error)
    except Exception:
        message = "(message could not be shown)"
    return f"{type(error).__name__}: {message[:MAX_MESSAGE]}"


def _limit_memory(memory_mb: int):
    """Caps the address space at memory_mb MiB where the OS allows it.
    The hard limit too, so the model cannot raise it again."""
    try:
        import resource

        limit = memory_mb << 20
        hard = resource.getrlimit(resource.RLIMIT_AS)[1]
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, AttributeError, ValueError, OSError):
        pass


def _read_frame(fd: int) -> bytes | None:
    """The next request's payload; None at end of input."""
    header = _read_exact(fd, 4)
    if header is None:
        return None
    return _read_exact(fd, struct.unpack(">I", header)[0])


def _read_exact(fd: int, size: int) -> bytes | None:
    chunks = []
    while size:
        chunk = os.read(fd, size)
        if not chunk:
            return None
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _encode(reply: dict) -> bytes:
    # ASCII JSON: a message with lone surrogates still encodes.
    return json.dumps(reply).encode("utf-8")


def _reply(fd: int, payload: bytes):
    data = struct.pack(">I", len(payload)) + payload
    while data:
        data = data[os.write(fd, data):]


if __name__ == "__main__":
    sys.exit(main())

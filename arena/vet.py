"""
Vetting a submission before anyone runs it.

A submission is a pickle (team.pkl) plus the .py files its classes come
from. Unpickling imports those files and runs their code, and a pickle can
also name any function in Python to call, so a submission is checked in two
ways first:

* scan_pickle lists every global the pickle refers to, without running
  any of it. Only the team's own modules and a short list of plain-data
  types are allowed (see ALLOWED); anything else, such as os.system or
  builtins.eval, is a violation and the submission is not run.
* scan_source reads the team's .py files with ast and flags what a human
  should look at: dangerous imports (os, subprocess, socket, ...), eval and
  exec, open(), reaching into internals (__class__, __globals__, _state,
  _getframe, ...), and code that runs while unpickling (__reduce__,
  __setstate__). Flags are not proof of anything, and a clean scan is not
  proof of safety: it decides what the organiser reads, not what is safe.

Neither is a sandbox. Run the bots under --launcher (bwrap, a container) for
real isolation; this keeps obvious attacks out and shows the code.
"""

import ast
import json
import os
import pickle
import re
import subprocess
import sys
from dataclasses import dataclass, field

MAX_PICKLE = 50 << 20  # bytes
MAX_SOURCE = 1 << 20  # bytes per .py file
MAX_FILES = 20  # .py files per team

# Globals a pickle may name besides the team's own modules: plain data.
ALLOWED = frozenset({
    "builtins.set", "builtins.frozenset", "builtins.dict", "builtins.list",
    "builtins.tuple", "builtins.int", "builtins.float", "builtins.complex",
    "builtins.str", "builtins.bytes", "builtins.bytearray", "builtins.bool",
    "builtins.object", "builtins.range", "builtins.slice",
    "collections.OrderedDict", "collections.defaultdict",
    "collections.deque", "collections.Counter",
    "copyreg._reconstructor", "_codecs.encode",
    "datetime.datetime", "datetime.date", "datetime.time",
    "datetime.timedelta", "datetime.timezone",
    "fractions.Fraction", "decimal.Decimal", "random.Random",
    "card_utils.Card", "player_action.PlayerAction",
    # numpy arrays and scalars
    "numpy.ndarray", "numpy.dtype",
    "numpy._core.multiarray._reconstruct",
    "numpy.core.multiarray._reconstruct",
    "numpy._core.multiarray.scalar", "numpy.core.multiarray.scalar",
})

# Modules a team's own files may not be called: the code that plays, the
# standard library, and this tool. A team file named random.py would
# replace the standard library inside its own worker.
RESERVED = frozenset({
    "card_utils", "engine", "game_interface", "game_state", "player_action",
    "arena", "evals", "submit", "tests",
}) | frozenset(sys.stdlib_module_names)

MODULE_FILE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")

SAFE_IMPORTS = frozenset({
    "__future__", "abc", "array", "bisect", "card_utils", "cmath",
    "collections", "copy", "dataclasses", "decimal", "enum", "fractions",
    "functools", "game_state", "heapq", "itertools", "math", "numbers",
    "numpy", "operator", "player_action", "pydantic", "random", "re",
    "statistics", "string", "time", "typing",
})
DANGEROUS_IMPORTS = frozenset({
    "os", "sys", "subprocess", "socket", "ctypes", "shutil", "pathlib",
    "io", "urllib", "http", "requests", "ftplib", "smtplib", "ssl",
    "asyncio", "threading", "multiprocessing", "concurrent", "signal",
    "resource", "gc", "inspect", "importlib", "builtins", "pickle",
    "cPickle", "marshal", "shelve", "pty", "platform", "tempfile", "glob",
    "webbrowser", "code", "codeop", "runpy", "atexit", "types", "dis",
    "mmap", "select", "selectors", "sqlite3", "zipfile", "tarfile",
    "msvcrt", "winreg", "posix", "nt", "fcntl", "pwd", "grp", "sysconfig",
    "site", "pkgutil", "zipimport", "imp", "ast", "weakref", "trace",
    "tracemalloc", "faulthandler", "logging",
})
DANGEROUS_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "breakpoint",
    "exit", "quit", "globals", "locals", "vars", "setattr", "delattr",
    "memoryview",
})
REVIEW_CALLS = frozenset({"getattr", "hasattr", "type", "super"})
DANGEROUS_ATTRIBUTES = frozenset({
    "__class__", "__subclasses__", "__globals__", "__builtins__",
    "__import__", "__code__", "__closure__", "__mro__", "__bases__",
    "__loader__", "__spec__", "_state", "_getframe", "f_globals", "f_locals",
    "f_back", "f_builtins", "gi_frame", "cr_frame", "tb_frame", "modules",
    "PUBLIC",
})
UNPICKLE_HOOKS = frozenset({
    "__reduce__", "__reduce_ex__", "__setstate__", "__getstate__",
    "__new__", "__init_subclass__", "__del__", "__getattr__",
    "__getattribute__", "__class_getitem__",
})


@dataclass
class Finding:
    file: str
    line: int
    severity: str  # "high": must be read; "medium": worth a look
    message: str

    def __str__(self):
        return f"{self.file}:{self.line}: [{self.severity}] {self.message}"


@dataclass
class Report:
    """What vetting found. ok means: nothing a human has to look at."""

    pickle_globals: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)  # pickle: refused
    findings: list[Finding] = field(default_factory=list)  # source: review
    problems: list[str] = field(default_factory=list)  # layout: refused

    @property
    def refused(self) -> bool:
        return bool(self.violations or self.problems)

    @property
    def needs_review(self) -> bool:
        return bool(self.findings)

    @property
    def ok(self) -> bool:
        return not self.refused and not self.needs_review

    def lines(self) -> list[str]:
        out = [f"refused: {p}" for p in self.problems]
        out += [f"refused: the pickle names {v}" for v in self.violations]
        out += [str(f) for f in self.findings]
        return out


# -- the pickle ---------------------------------------------------------


class _Thing:
    """Stands in for every object a pickle builds. It does nothing, so
    reading a pickle with it runs no code from the pickle."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Thing()

    def __setstate__(self, state):
        pass

    def __setitem__(self, key, value):
        pass

    def append(self, *args):
        pass

    def extend(self, *args):
        pass

    def add(self, *args):
        pass

    def update(self, *args, **kwargs):
        pass


class _Recorder(pickle.Unpickler):
    """Unpickles with every global replaced by _Thing, noting each one."""

    seen: set

    def find_class(self, module, name):
        self.seen.add(f"{module}.{name}")
        if module in ("builtins", "__builtin__") and name in (
            "set", "frozenset", "dict", "list", "tuple", "int", "float",
            "complex", "str", "bytes", "bytearray", "bool", "object",
            "range", "slice",
        ):
            return getattr(__import__("builtins"), name)
        return _Thing

    def persistent_load(self, pid):
        raise pickle.UnpicklingError("persistent ids are not allowed")


def pickle_globals(path: str) -> list[str]:
    """Every "module.name" the pickle refers to, found without running any
    of it. Do not call this on an untrusted file in a process you care
    about: use scan_pickle, which runs it in a child process with a time
    limit."""
    import io

    with open(path, "rb") as file:
        data = file.read()
    recorder = _Recorder(io.BytesIO(data))
    recorder.seen = set()
    recorder.load()
    return sorted(recorder.seen)


def _is_allowed(name: str, own_modules, extra) -> bool:
    # Pickle protocol 2 spells builtins as __builtin__.
    name = name.replace("__builtin__.", "builtins.", 1)
    module = name.rpartition(".")[0]
    if name in ALLOWED or module in own_modules:
        return True
    for entry in extra:
        if name == entry or module == entry or module.startswith(entry + "."):
            return True
    return False


def scan_pickle(path, own_modules=(), allow=(), timeout=20.0) -> tuple:
    """(globals, violations) for a pickle file. The file is read in a child
    process under a time limit, so a hostile pickle cannot hang or crash
    the caller. violations are the globals that are neither the team's own
    modules, nor in ALLOWED, nor in allow."""
    path = os.path.abspath(os.fspath(path))
    if os.path.getsize(path) > MAX_PICKLE:
        return [], [f"a file over {MAX_PICKLE >> 20} MB"]
    try:
        done = subprocess.run(
            [sys.executable, "-m", "arena.vet", path],
            capture_output=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    except subprocess.TimeoutExpired:
        return [], ["a pickle that takes too long to read (a pickle bomb?)"]
    try:
        names = json.loads(done.stdout.decode())["globals"]
    except (ValueError, KeyError):
        return [], ["a pickle that cannot be read"]
    bad = [n for n in names if not _is_allowed(n, set(own_modules), allow)]
    return names, bad


# -- the source ---------------------------------------------------------


def scan_source(path, name=None, own_modules=()) -> list[Finding]:
    """What in one .py file a human should read. own_modules: the team's
    other files, which it may import freely (they are read too)."""
    name = name or os.path.basename(path)
    with open(path, "rb") as file:
        raw = file.read()
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError) as error:
        return [Finding(name, 1, "high", f"does not parse: {error}")]
    findings: list[Finding] = []

    def add(node, severity, message):
        findings.append(
            Finding(name, getattr(node, "lineno", 1), severity, message)
        )

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if isinstance(node, ast.ImportFrom) and node.level:
                add(node, "medium", "relative import")
            for module in modules:
                top = module.split(".")[0]
                if top in own_modules:
                    continue
                if top in DANGEROUS_IMPORTS:
                    add(node, "high", f"imports {module}")
                elif top not in SAFE_IMPORTS and top:
                    add(node, "medium", f"imports {module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in DANGEROUS_CALLS:
                    add(node, "high", f"calls {func.id}()")
                elif func.id in REVIEW_CALLS:
                    add(node, "medium", f"calls {func.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in DANGEROUS_ATTRIBUTES:
                add(node, "high", f"reaches into .{node.attr}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in UNPICKLE_HOOKS:
                add(node, "medium",
                    f"defines {node.name}, which can run while unpickling")
        elif isinstance(node, ast.Name):
            if node.id in ("__builtins__", "__loader__", "__spec__"):
                add(node, "high", f"uses {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if re.search(r"(https?://|ftp://|\\\\\\\\|/etc/|/bin/|cmd\.exe|"
                         r"powershell|\.ssh|id_rsa)", text, re.I):
                add(node, "medium", f"mentions {text[:40]!r}")
    return findings


# -- a whole submission -------------------------------------------------


def vet(pickle_path, source_paths, allow=()) -> Report:
    """Vets one submission: team.pkl and the .py files that came with it."""
    report = Report()
    names = [os.path.basename(p) for p in source_paths]
    if len(names) > MAX_FILES:
        report.problems.append(f"more than {MAX_FILES} .py files")
    for name, path in zip(names, source_paths):
        if not MODULE_FILE.match(name):
            report.problems.append(
                f"{name}: a module file is letters, digits and _ only, "
                "ending .py (no folders)"
            )
        elif name[:-3] in RESERVED:
            report.problems.append(
                f"{name}: a file named like a standard library or arena "
                "module cannot be used; rename it"
            )
        if os.path.getsize(path) > MAX_SOURCE:
            report.problems.append(f"{name}: over {MAX_SOURCE >> 10} KB")
    if report.problems:
        return report
    own = {name[:-3] for name in names}
    report.pickle_globals, report.violations = scan_pickle(
        pickle_path, own, allow
    )
    report.violations = [
        v + " (your Model class was defined in a script you ran directly: "
        "keep it in model.py and import it before pickling)"
        if v.startswith("__main__.") else v
        for v in report.violations
    ]
    for name, path in zip(names, source_paths):
        report.findings += scan_source(path, name, own)
    return report


if __name__ == "__main__":
    # The child process of scan_pickle: reads one pickle, prints its globals.
    try:
        found = pickle_globals(sys.argv[1])
        print(json.dumps({"globals": found}))
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        sys.exit(1)

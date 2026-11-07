"""
The organiser's side of the event: take in the teams' uploads, vet them,
play the round, and publish the results site.

    python -m arena.event import DOWNLOADS     # team.pkl + .py files
    python -m arena.event status               # who is in, who needs review
    python -m arena.event review TEAM          # findings and the code itself
    python -m arena.event approve TEAM         # after reading it
    python -m arena.event run --deals 200      # every bot plays every other
    python -m arena.event export --out site    # results site (delayed live)

Everything lives under --root (default event/):

    teams.json              one entry per team: status and what was found
    versions/TEAM/DIGEST/   every upload kept: TEAM.pkl and its .py files
    pairings.jsonl          one line per finished pairing (a MatchLog)
    hands/A__B.jsonl.gz     every hand of a pairing, for replays
    run.json                the round: which versions, deals, seed

A team uploads TEAM.pkl plus the .py files its classes come from. Nothing
runs until it has been vetted (arena.vet): the pickle may only name the
team's own modules and plain data; the code is read for dangerous
imports and calls, and a team with findings waits for `approve`.
"""

import argparse
import dataclasses
import hashlib
import itertools
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from arena import vet
from arena.match import HandRecord, MatchConfig, MatchLog, play_match
from arena.players import Player
from arena.remote import TIME_LIMIT
from arena.replays import save_hands, site_hand
from arena.results import ResultStore, _decode
from evals.score import score

TEAM_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Google Forms names an upload "<file name> - <respondent>.<ext>".
FORM_NAME = re.compile(r"^(?P<stem>.+) - (?P<who>.+?)(?: \(\d+\))?$")
DUMMIES_TO_CHECK = ("fold", "shove", "random")


# -- the uploads -----------------------------------------------------------


@dataclass
class Upload:
    team: str
    pkl: str
    sources: list[str]  # .py files, with the names the team gave them
    uploaded: float  # newest modification time among the files
    names: dict[str, str] = field(default_factory=dict)  # path -> real name


def _clean_name(filename: str) -> tuple[str, str | None]:
    """("model.py" or "model - Jane Doe.py") -> ("model.py", "Jane Doe")."""
    stem, ext = os.path.splitext(filename)
    found = FORM_NAME.match(stem)
    if found:
        return found["stem"] + ext, found["who"]
    return filename, None


def find_uploads(folder: str) -> list[Upload]:
    """Group what is in folder into one Upload per team. Either one
    subfolder per team (TEAM.pkl and its .py files inside), or the flat
    folder Google Forms makes, where each file is named "<name> - <who>"
    and one respondent's files belong together."""
    uploads: list[Upload] = []
    flat: dict[str, list[tuple[str, str]]] = {}  # who -> (path, clean name)
    for current, _, files in sorted(os.walk(folder)):
        pkls = [f for f in files if f.endswith(".pkl")]
        if current != folder and pkls:
            # A team folder: its files, as named.
            paths = [os.path.join(current, f) for f in files]
            for pkl in pkls:
                team = _clean_name(pkl)[0][:-4]
                sources = [
                    p for p in paths if p.endswith(".py")
                ]
                uploads.append(
                    _upload(team, os.path.join(current, pkl), sources))
            continue
        for filename in files:
            if filename.endswith((".pkl", ".py")):
                name, who = _clean_name(filename)
                flat.setdefault(who or "", []).append(
                    (os.path.join(current, filename), name)
                )
    for who, entries in sorted(flat.items()):
        names = dict(entries)
        pkls = sorted((p for p in names if p.endswith(".pkl")),
                      key=os.path.getmtime)
        if not pkls:
            continue
        newest = pkls[-1]
        uploads.append(_upload(
            names[newest][:-4], newest,
            [p for p in names if p.endswith(".py")], names,
        ))
    uploads.sort(key=lambda u: (u.uploaded, u.team))
    return uploads


def _upload(team, pkl, sources, names=None) -> Upload:
    names = names or {p: os.path.basename(p) for p in sources}
    mtime = max(os.path.getmtime(p) for p in [pkl, *sources])
    return Upload(team, pkl, sorted(sources), mtime,
                  {p: names.get(p, os.path.basename(p)) for p in sources})


# -- the organiser's store ---------------------------------------------------


class Event:
    """The folder that holds one event."""

    def __init__(self, root: str = "event"):
        self.root = root
        self.lock = threading.Lock()

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    # teams.json: {team: {"current", "approved": [..], "latest": {..}}}
    def teams(self) -> dict:
        try:
            with open(self.path("teams.json"), encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return {}

    def save_teams(self, teams: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        temp = self.path("teams.json.tmp")
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(teams, file, indent=1, sort_keys=True)
        os.replace(temp, self.path("teams.json"))

    def version_dir(self, team: str, digest: str) -> str:
        return self.path("versions", team, digest)

    def ready(self) -> dict[str, str]:
        """team -> path of the pickle that plays, for every ready team."""
        out = {}
        for team, entry in sorted(self.teams().items()):
            if entry.get("current"):
                out[team] = os.path.join(
                    self.version_dir(team, entry["current"]), f"{team}.pkl"
                )
        return out


def digest_of(upload: Upload) -> str:
    """One hash over the pickle and every source file (names and bytes)."""
    sha = hashlib.sha256()
    pairs = [(f"{upload.team}.pkl", upload.pkl)] + sorted(
        (upload.names[p], p) for p in upload.sources
    )
    for name, path in pairs:
        with open(path, "rb") as file:
            data = file.read()
        sha.update(name.encode() + b"\0" + data + b"\0")
    return sha.hexdigest()[:12]


def stage(event: Event, upload: Upload, digest: str) -> list[str]:
    """Copies an upload to versions/TEAM/DIGEST/ under its real file names;
    returns the .py paths there."""
    folder = event.version_dir(upload.team, digest)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    shutil.copyfile(upload.pkl, os.path.join(folder, f"{upload.team}.pkl"))
    copied = []
    for path in upload.sources:
        target = os.path.join(folder, upload.names[path])
        shutil.copyfile(path, target)
        copied.append(target)
    return copied


def validate(pkl_path: str, deals: int = 10,
             timeout: float = TIME_LIMIT) -> str | None:
    """Plays the pickle against dummy bots in its own worker. None if it
    played legally in time every time, else the first fault. Runs the
    team's code: vet first."""
    team = os.path.splitext(os.path.basename(pkl_path))[0]
    for dummy in DUMMIES_TO_CHECK:
        me = Player(team, pkl_path)
        other = Player(dummy, f"dummy:{dummy}")
        try:
            result = score([play_match(
                [me, other], MatchConfig(deals=deals, timeout=timeout))])
        except Exception as error:  # a worker that cannot even start
            return f"against {dummy}: {type(error).__name__}: {error}"
        if result.faults[0]:
            return f"against {dummy}: {result.first_faults[0]}"
    return None


@dataclass
class Outcome:
    team: str
    status: str  # ready | needs_review | refused | invalid | unchanged
    reason: str
    findings: list[str]


def process(event: Event, upload: Upload, allow: Sequence[str] = (),
            review_all: bool = False, deals: int = 10) -> Outcome:
    """Vets and checks one upload and records the result."""
    if not TEAM_NAME.match(upload.team):
        return Outcome(
            upload.team, "refused",
            "team names are 1-32 characters of a-z, 0-9, '-' and '_'", [])
    teams = event.teams()
    entry = teams.setdefault(
        upload.team, {"current": None, "approved": [], "latest": None}
    )
    digest = digest_of(upload)
    latest = entry["latest"]
    if (latest and latest["digest"] == digest
            and latest["status"] in ("ready", "refused", "needs_review")):
        return Outcome(upload.team, "unchanged", latest["reason"],
                       latest["findings"])
    sources = stage(event, upload, digest)
    pkl = os.path.join(event.version_dir(upload.team, digest),
                       f"{upload.team}.pkl")
    report = vet.vet(pkl, sources, allow)
    findings = report.lines()
    if report.refused:
        status, reason = "refused", "; ".join(findings[:3])
    elif (report.needs_review or review_all) and digest not in entry[
            "approved"]:
        status = "needs_review"
        reason = (f"{len(report.findings)} thing(s) to read; "
                  f"run: review {upload.team}, then approve {upload.team}")
        if not report.findings:
            reason = f"run: review {upload.team}, then approve {upload.team}"
    else:
        problem = validate(pkl, deals)
        if problem:
            status, reason = "invalid", problem
        else:
            status, reason = "ready", "plays legally"
            entry["current"] = digest
    entry["latest"] = _latest(digest, status, reason, findings)
    event.save_teams(teams)
    return Outcome(upload.team, status, reason, findings)


def _latest(digest, status, reason, findings) -> dict:
    return {"digest": digest, "status": status, "reason": reason,
            "findings": findings, "at": time.time()}


def import_folder(event: Event, folder: str, **options) -> list[Outcome]:
    """Takes in every upload under folder, oldest first (so a team's newest
    upload wins, and a newer one that fails never replaces a working one)."""
    return [process(event, upload, **options)
            for upload in find_uploads(folder)]


def approve(event: Event, team: str, deals: int = 10) -> Outcome:
    """Marks the team's latest upload as read and OK, and checks it plays."""
    teams = event.teams()
    entry = teams.get(team)
    if not entry or not entry.get("latest"):
        raise SystemExit(f"no upload from {team!r}")
    latest = entry["latest"]
    if latest["status"] == "refused":
        raise SystemExit(f"{team}: refused ({latest['reason']}); it cannot "
                         "be approved: ask the team to fix it")
    digest = latest["digest"]
    if digest not in entry["approved"]:
        entry["approved"].append(digest)
    pkl = os.path.join(event.version_dir(team, digest), f"{team}.pkl")
    problem = validate(pkl, deals)
    if problem:
        entry["latest"] = _latest(digest, "invalid", problem,
                                  latest["findings"])
    else:
        entry["current"] = digest
        entry["latest"] = _latest(digest, "ready",
                                  "approved; plays legally",
                                  latest["findings"])
    event.save_teams(teams)
    status = entry["latest"]["status"]
    return Outcome(team, status, entry["latest"]["reason"],
                   latest["findings"])


# -- the round ---------------------------------------------------------------


def pair_key(a: str, b: str) -> str:
    return f"{a}__{b}"


def load_pairings(event: Event) -> list[dict]:
    """Every finished pairing (a, b, finished_at, log as a MatchLog)."""
    out = []
    try:
        with open(event.path("pairings.jsonl"), encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    record = json.loads(line)
                    record["log"] = _decode(record["log"])
                    out.append(record)
    except FileNotFoundError:
        pass
    return out


def start_run(event: Event, deals: int, seed: int,
              restart: bool = False) -> dict:
    """Creates (or checks) run.json: who plays, with which versions."""
    ready = event.ready()
    entries = event.teams()
    teams = [{"team": t, "digest": entries[t]["current"]} for t in ready]
    run = {"started_at": time.time(), "deals": deals, "seed": seed,
           "teams": teams, "finished_at": None}
    try:
        with open(event.path("run.json"), encoding="utf-8") as file:
            old = json.load(file)
    except FileNotFoundError:
        old = None
    if old and not restart:
        same = (old["deals"], old["seed"], old["teams"]) == (
            deals, seed, teams)
        if not same:
            raise SystemExit(
                "a round was already started with different teams, deals or "
                "seed; use --restart to throw its results away"
            )
        return old
    if old and restart:
        for name in ("pairings.jsonl", "results.jsonl"):
            if os.path.exists(event.path(name)):
                os.remove(event.path(name))
        shutil.rmtree(event.path("hands"), ignore_errors=True)
    os.makedirs(event.root, exist_ok=True)
    with open(event.path("run.json"), "w", encoding="utf-8") as file:
        json.dump(run, file, indent=1)
    return run


def run_round(event: Event, deals: int = 200, seed: int = 0,
              concurrency: int = 2, timeout: float = TIME_LIMIT,
              launcher: Sequence[str] = (), memory_mb: int = 2048,
              restart: bool = False, log=print) -> int:
    """Plays every pairing of the ready teams that has not been played yet
    (so it can be stopped and started again). Returns pairings played."""
    run = start_run(event, deals, seed, restart)
    ready = event.ready()
    if len(ready) < 2:
        raise SystemExit("need at least two ready teams (see: status)")
    names = [t["team"] for t in run["teams"]]
    done = {pair_key(r["a"], r["b"]) for r in load_pairings(event)}
    todo = [(a, b) for a, b in itertools.combinations(names, 2)
            if pair_key(a, b) not in done]
    log(f"{len(names)} teams, {len(done)} of "
        f"{len(done) + len(todo)} pairings already played")
    store = ResultStore(event.path("results.jsonl"))
    config = MatchConfig(deals=deals, seed=seed, timeout=timeout,
                         launcher=tuple(launcher), memory_mb=memory_mb)
    played = 0

    def play(pair):
        a, b = pair
        hands: list[dict] = []

        def record(rec: HandRecord):
            hands.append(site_hand(rec, len(hands)))

        match = play_match(
            [Player(a, ready[a]), Player(b, ready[b])], config, record)
        save_hands(event.path("hands", pair_key(a, b) + ".jsonl.gz"), hands)
        entry = {"a": a, "b": b, "finished_at": time.time(),
                 "deals": deals, "seed": seed, "hands": len(hands),
                 "log": dataclasses.asdict(match)}
        with event.lock:
            with open(event.path("pairings.jsonl"), "a",
                      encoding="utf-8") as file:
                file.write(json.dumps(entry) + "\n")
            store.add(match)
        return a, b, match

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for a, b, match in pool.map(play, todo):
            played += 1
            won = sum(h.net[h.seats.index(a)] for h in match.hands)
            log(f"[{len(done) + played}/{len(done) + len(todo)}] "
                f"{a} vs {b}: {a} {won / len(match.hands):+.1f} chips/hand")
    run["finished_at"] = time.time()
    with open(event.path("run.json"), "w", encoding="utf-8") as file:
        json.dump(run, file, indent=1)
    return played


# -- the command line --------------------------------------------------------


def _status_lines(event: Event) -> list[str]:
    teams = event.teams()
    lines = []
    for team, entry in sorted(teams.items()):
        latest = entry.get("latest") or {}
        mark = "READY" if entry.get("current") else latest.get(
            "status", "?").upper()
        note = latest.get("reason", "")
        if entry.get("current") and latest.get("status") not in (
                None, "ready"):
            note = f"(plays its earlier upload) newer: {latest['status']}: "\
                   f"{note}"
        lines.append(f"  {mark:<13} {team:<24} {note}")
    return lines or ["  (no uploads yet)"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m arena.event")
    parser.add_argument("--root", default="event")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="take in the teams' uploads")
    p.add_argument("folder")
    p.add_argument("--allow", action="append", default=[], metavar="MODULE",
                   help="also allow this module (or module.name) in pickles")
    p.add_argument("--review-all", action="store_true",
                   help="every team waits for approve, flagged or not")

    p = sub.add_parser("status", help="who is in")

    p = sub.add_parser("review", help="what was found, and the code")
    p.add_argument("team")

    p = sub.add_parser("approve", help="you have read it: let it play")
    p.add_argument("team")

    p = sub.add_parser("run", help="every ready team plays every other")
    p.add_argument("--deals", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--concurrency", type=int,
                   default=max(1, (os.cpu_count() or 2) // 2))
    p.add_argument("--timeout", type=float, default=TIME_LIMIT)
    p.add_argument("--memory-mb", type=int, default=2048)
    p.add_argument("--launcher", default="",
                   help='sandbox prefix, e.g. "bwrap --ro-bind / /"')
    p.add_argument("--restart", action="store_true",
                   help="throw the round's results away and start again")

    p = sub.add_parser("export", help="write the results site")
    p.add_argument("--out", default="site_out")
    p.add_argument("--title", default="Poker Bot Competition")
    p.add_argument("--delay", type=float, default=0.0,
                   help="seconds after a pairing ends before it is shown")
    p.add_argument("--spacing", type=float, default=0.0,
                   help="at least this many seconds between pairings shown")
    p.add_argument("--max-hands", type=int, default=60,
                   help="replay hands per pairing (biggest swings); 0 = all")
    p.add_argument("--event-file", default="event.json")
    p.add_argument("--guide", default="README.md")
    p.add_argument("--loop", type=float, metavar="SECONDS",
                   help="keep exporting every SECONDS")
    p.add_argument("--form-url", default="", metavar="URL",
                   help="the submission form (or set POKER_FORM_URL); "
                        "published only while submissions are open")
    p.add_argument("--submissions", choices=["closed", "open", "over"],
                   default="closed",
                   help="what the site says about submitting: closed (not "
                        "open yet; the default), open (shows the form link) "
                        "or over (the deadline has passed)")
    p.add_argument("--git-push", action="store_true")

    args = parser.parse_args(argv)
    event = Event(args.root)
    if args.command == "import":
        if not os.path.isdir(args.folder):
            raise SystemExit(f"not a folder: {args.folder}")
        for out in import_folder(event, args.folder, allow=args.allow,
                                 review_all=args.review_all):
            print(f"  {out.status.upper():<13} {out.team:<24} {out.reason}")
        print("\n" + "\n".join(_status_lines(event)))
    elif args.command == "status":
        print("\n".join(_status_lines(event)))
        try:
            with open(event.path("run.json"), encoding="utf-8") as file:
                run = json.load(file)
            total = len(run["teams"]) * (len(run["teams"]) - 1) // 2
            print(f"\nround: {len(load_pairings(event))}/{total} pairings, "
                  f"{run['deals']} deals each"
                  + (", finished" if run["finished_at"] else ""))
        except FileNotFoundError:
            print("\nthe round has not started")
    elif args.command == "review":
        entry = event.teams().get(args.team)
        if not entry or not entry.get("latest"):
            raise SystemExit(f"no upload from {args.team!r}")
        latest = entry["latest"]
        print(f"{args.team}: {latest['status']} ({latest['reason']})")
        for line in latest["findings"]:
            print("  " + line)
        folder = event.version_dir(args.team, latest["digest"])
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                print(f"\n===== {name} =====")
                with open(os.path.join(folder, name),
                          encoding="utf-8", errors="replace") as file:
                    for number, line in enumerate(file, 1):
                        print(f"{number:4}  {line.rstrip()}")
    elif args.command == "approve":
        out = approve(event, args.team)
        print(f"{out.team}: {out.status} ({out.reason})")
    elif args.command == "run":
        run_round(event, args.deals, args.seed, args.concurrency,
                  args.timeout, tuple(__import__("shlex").split(
                      args.launcher)), args.memory_mb, args.restart)
    elif args.command == "export":
        from arena.site_export import export_loop

        export_loop(event, args)


if __name__ == "__main__":
    main()

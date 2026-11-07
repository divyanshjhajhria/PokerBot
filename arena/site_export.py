"""
The results site: a folder of plain files (the site/ pages plus data/*.json)
that any static host can serve.

Everything is shown with an artificial delay so the site feels live without
giving anything away early. Each finished pairing becomes visible
`delay` seconds after it ended, and no sooner than `spacing` seconds after
the pairing before it, so a round that takes minutes to play can be watched
over as long as you like. Standings are computed from the pairings that are
visible, the champion appears when the last one is, and a replay of every
visible pairing is published with its biggest hands.

Only what the site needs leaves the machine: team names, scores, hands
(hole cards only at a showdown), never code or pickles.
"""

import hashlib
import json
import math
import os
import subprocess
import time
from dataclasses import dataclass

from arena.event import Event, load_pairings
from arena.replays import load_hands, replay_file
from evals.score import leaderboard, score

SCHEMA = 1
SITE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site"
)
ORGANISER_HEADING = "\n## For the organiser"


@dataclass
class Options:
    title: str = "Poker Bot Competition"
    delay: float = 0.0
    spacing: float = 0.0
    max_hands: int = 60
    event: dict | None = None  # the Home page text, see load_event
    guide: str = ""  # markdown for the How to submit page
    # The submission form's address. It is NEVER written to a file in the
    # repository; it appears in the published data only while submissions
    # are open (see submission_state).
    form_url: str = ""
    submissions: str = "closed"  # closed (not yet), open, or over


def load_event(path: str) -> dict:
    """Reads and tidies the Home page text (event.json): only known fields,
    as short plain strings, so a typo cannot break or poison the page."""
    try:
        with open(path, encoding="utf-8-sig") as file:
            raw = json.load(file)
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read the event file {path}: {error}")
    if not isinstance(raw, dict):
        raise SystemExit("the event file must hold one JSON object")

    def text(value, limit=1200):
        return value.strip()[:limit] if isinstance(value, str) else ""

    def texts(value):
        items = value if isinstance(value, list) else []
        return [t for t in (text(v) for v in items[:20]) if t]

    timeline = []
    for row in (raw.get("timeline") or [])[:30]:
        if isinstance(row, dict) and text(row.get("title")):
            timeline.append({
                "time": text(row.get("time"), 40),
                "title": text(row.get("title"), 120),
                "detail": text(row.get("detail")),
            })
    repo = text(raw.get("repo"), 200)
    whatsapp = text(raw.get("whatsapp"), 200)
    event = {
        "tagline": text(raw.get("tagline"), 200),
        "intro": texts(raw.get("intro")),
        "prize": text(raw.get("prize"), 200),
        "timeline": timeline,
        "rules": texts(raw.get("rules")),
        "contact": text(raw.get("contact")),
        "repo": repo if repo.startswith("https://") else "",
        "whatsapp": whatsapp if whatsapp.startswith("https://") else "",
    }
    window = raw.get("submissions")
    if isinstance(window, dict):
        # Display text only ("16:00"): the organiser decides when submissions
        # open and close by running the export with --submissions.
        event["submissions"] = {
            "opens": text(window.get("opens"), 40),
            "closes": text(window.get("closes"), 40),
        }
    return event


def submission_state(event: dict | None, form_url: str,
                     status: str = "closed") -> dict:
    """Whether teams may submit, and the form's address when they may.

    status is what the organiser says: "closed" (not open yet), "open" or
    "over". The address is released only while it is "open", so it cannot be
    found on the site, or in its git history, before then."""
    window = (event or {}).get("submissions") or {}
    is_open = status == "open"
    return {
        "status": "open" if is_open else "closed",
        "why": None if is_open else ("over" if status == "over"
                                     else "not_yet"),
        "opens": window.get("opens", ""),
        "closes": window.get("closes", ""),
        "form_url": form_url if is_open and form_url.startswith(
            "https://") else None,
    }


def _json(value) -> str:
    """Strictly valid JSON: infinities and NaN become null."""

    def clean(item):
        if isinstance(item, float) and not math.isfinite(item):
            return None
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        return item

    return json.dumps(clean(value), separators=(",", ":"), allow_nan=False)


def _write_if_changed(path: str, text: str) -> bool:
    try:
        with open(path, encoding="utf-8") as file:
            if file.read() == text:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)
    os.replace(temp, path)
    return True


def _sync_assets(out: str) -> bool:
    """Copies the site's pages (site/) into out; True if anything changed."""
    changed = False
    for current, _, files in os.walk(SITE_DIR):
        for name in files:
            if name == "package.json":  # dev-only (lets Node import the modules)
                continue
            source = os.path.join(current, name)
            target = os.path.join(out, os.path.relpath(source, SITE_DIR))
            with open(source, "rb") as file:
                data = file.read()
            try:
                with open(target, "rb") as file:
                    if file.read() == data:
                        continue
            except FileNotFoundError:
                pass
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as file:
                file.write(data)
            changed = True
    return changed


# -- what is visible when ----------------------------------------------------


def visible_pairings(pairings: list[dict], now: float, delay: float,
                     spacing: float) -> list[dict]:
    """The pairings that may be shown at time now, oldest first, each with
    its number ("n", from 1). A pairing shows delay seconds after it ended
    and at least spacing seconds after the one before it."""
    shown = []
    at = None
    for number, pairing in enumerate(
            sorted(pairings, key=lambda p: (p["finished_at"], p["a"], p["b"])),
            1):
        earliest = pairing["finished_at"] + delay
        at = earliest if at is None else max(earliest, at + spacing)
        if at <= now:
            shown.append({**pairing, "n": number, "visible_at": at})
    return shown


def _verdict(mean, half) -> str:
    if mean is None or half is None or not math.isfinite(half):
        return "tie"
    if mean - half > 0:
        return "a"
    if mean + half < 0:
        return "b"
    return "tie"


def _tied(upper, lower) -> bool:
    if upper["score"] is None or lower["score"] is None:
        return False
    if upper["ci95"] is None or lower["ci95"] is None:
        return True
    return upper["score"] - lower["score"] < math.hypot(
        upper["ci95"], lower["ci95"])


def build_board(names: list[str], digests: dict, shown: list[dict],
                total: int) -> dict:
    """The ranking of everyone over the pairings in shown (bb/100)."""
    logs = [p["log"] for p in shown]
    pooled = {s.name: s for s in leaderboard(logs)} if logs else {}
    cells: dict[tuple, dict] = {}
    faults = {name: 0 for name in names}
    for pairing in shown:
        result = score([pairing["log"]])
        a, b = result.names
        for me, other, i in ((a, b, 0), (b, a, 1)):
            cells[(me, other)] = {"mean_bb": result.bb_per_100[i],
                                  "ci95_bb": result.ci95[i]}
            faults[me] += result.faults[i]
    teams = []
    for name in names:
        mine = {o: c for (m, o), c in cells.items() if m == name}
        wins = sum(1 for c in mine.values()
                   if c["mean_bb"] - c["ci95_bb"] > 0)
        losses = sum(1 for c in mine.values()
                     if c["mean_bb"] + c["ci95_bb"] < 0)
        standing = pooled.get(name)
        teams.append({
            "team": name,
            "version": digests.get(name),
            "score": standing.bb_per_100 if standing else None,
            "ci95": standing.ci95 if standing else None,
            "score_mbb": None,
            "wins": wins,
            "draws": len(mine) - wins - losses,
            "losses": losses,
            "opponents_played": len(mine),
            "opponents_expected": len(names) - 1,
            "faults": faults[name],
            "disabled_in": [],
            "incomplete": [],
            "think_seconds": 0,
            "tied_with_next": False,
        })
    teams.sort(key=lambda t: (t["score"] is None, -(t["score"] or 0),
                              t["team"]))
    for rank, team in enumerate(teams, 1):
        team["rank"] = rank
    for upper, lower in zip(teams, teams[1:]):
        upper["tied_with_next"] = _tied(upper, lower)
    return {
        "kind": "group",
        "provisional": len(shown) < total,
        "freeze_id": 1,
        "pairs_total": total,
        "pairs_done": len(shown),
        "cycle": None,
        "teams": teams,
        "matrix": {
            me: {other: cells.get((me, other)) for other in names
                 if other != me}
            for me in names
        },
    }


def build(event: Event, now: float, options: Options) -> tuple[dict, dict]:
    """(data files {name: json text}, replays {pairing id: replay dict})."""
    try:
        with open(event.path("run.json"), encoding="utf-8") as file:
            run = json.load(file)
    except FileNotFoundError:
        run = None
    ready = list(event.ready())
    entries = event.teams()
    if run:
        names = sorted(t["team"] for t in run["teams"])
        digests = {t["team"]: t["digest"] for t in run["teams"]}
    else:
        names = ready
        digests = {t: entries[t]["current"] for t in ready}
    total = len(names) * (len(names) - 1) // 2
    shown = visible_pairings(
        load_pairings(event), now, options.delay, options.spacing
    ) if run else []
    done = bool(run) and total > 0 and len(shown) == total
    phase = "done" if done else "groups" if run else "registration"
    board = build_board(names, digests, shown, total) if shown else None

    pairing_rows = []
    replays = {}
    for pairing in reversed(shown):  # newest first
        log = pairing["log"]
        pid = f"group-{pairing['n']}"
        result = score([log])
        mean, half = result.bb_per_100[0], result.ci95[0]
        pairing_rows.append({
            "id": pid,
            "job_id": pairing["n"],
            "kind": "group",
            "a": result.names[0],
            "b": result.names[1],
            "group": "A",
            "deals": log.config.deals,
            "deals_completed": log.config.deals,
            "rounds": 1,
            "hands": len(log.hands),
            "mean_bb": mean,
            "ci95_bb": half,
            "verdict": _verdict(mean, half),
            "faults_a": result.faults[0],
            "faults_b": result.faults[1],
            "disabled_a": None,
            "disabled_b": None,
            "status": "complete",
            "finished_at": pairing["visible_at"],
            "replay": None,
        })
        hands = load_hands(event.path(
            "hands", f"{pairing['a']}__{pairing['b']}.jsonl.gz"))
        if hands:
            replays[pid] = replay_file(
                pid, pairing["a"], pairing["b"], hands, options.max_hands)
            pairing_rows[-1]["replay"] = f"data/replays/{pid}.json"

    champion = board["teams"][0]["team"] if done and board else None
    view = {
        "phase": phase,
        "rounds": 1,
        "advance": 1,
        "groups": (
            [{"name": "A", "teams": names, "board": board}] if run else []
        ),
        "qualified": [],
        "finals": None,
        "champion": champion,
    }
    roster = {
        "teams": [
            {"team": name, "status": "submitted",
             "group": "A" if run else None, "finalist": False,
             "current": None, "frozen": None, "sync": None, "versions": []}
            for name in names
        ],
        "registrations": [],
    }
    files = {
        "tournament.json": _json(view),
        "teams.json": _json(roster),
    }
    if shown:
        files["pairings-group.json"] = _json(pairing_rows)
    return files, replays


def export_site(event: Event, out: str, options: Options,
                now: float | None = None) -> bool:
    """Writes the site into out. True if anything changed."""
    now = time.time() if now is None else now
    data = os.path.join(out, "data")
    os.makedirs(data, exist_ok=True)
    changed = _sync_assets(out)
    files, replays = build(event, now, options)

    for name in ("pairings-group.json", "leaderboard-final.json",
                 "pairings-final.json", "pairings-ladder.json",
                 "leaderboard-ladder.json"):
        stale = os.path.join(data, name)
        if name not in files and os.path.exists(stale):
            os.remove(stale)
            changed = True
    for name, text in files.items():
        changed = _write_if_changed(os.path.join(data, name), text) or changed

    folder = os.path.join(data, "replays")
    wanted = set()
    for pid, replay in replays.items():
        wanted.add(f"{pid}.json")
        changed = _write_if_changed(
            os.path.join(folder, f"{pid}.json"), _json(replay)) or changed
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.endswith(".json") and name not in wanted:
                os.remove(os.path.join(folder, name))
                changed = True

    guide = options.guide.split(ORGANISER_HEADING)[0]
    guide_path = os.path.join(data, "submitting.md")
    if guide.strip():
        changed = _write_if_changed(guide_path, guide) or changed
    elif os.path.exists(guide_path):
        os.remove(guide_path)
        changed = True
    event_path = os.path.join(data, "event.json")
    if options.event:
        changed = _write_if_changed(event_path, _json(options.event)) or changed
    elif os.path.exists(event_path):
        os.remove(event_path)
        changed = True

    view = json.loads(files["tournament.json"])
    shown_count = (
        len(json.loads(files["pairings-group.json"]))
        if "pairings-group.json" in files else 0
    )
    fingerprint = hashlib.sha256(
        "".join(files[n] for n in sorted(files)).encode()
        + guide.encode()
        + _json(options.event or {}).encode()
        + _json(submission_state(
            options.event, options.form_url, options.submissions)).encode()
        + options.title.encode()
        + "".join(sorted(replays)).encode()
        + _json([r["shown"] for r in replays.values()]).encode()
    ).hexdigest()[:16]
    manifest_path = os.path.join(data, "manifest.json")
    generated_at = now
    try:
        with open(manifest_path, encoding="utf-8") as file:
            previous = json.load(file)
        if previous.get("fingerprint") == fingerprint:
            generated_at = previous.get("generated_at", now)
        else:
            changed = True
    except (FileNotFoundError, ValueError):
        changed = True
    submissions = submission_state(
        options.event, options.form_url, options.submissions)
    manifest = {
        "schema": SCHEMA,
        "title": options.title,
        "submissions": submissions,
        "submit_text": "",
        "has_guide": bool(guide.strip()),
        "has_event": bool(options.event),
        "registry_repo": None,
        "register_form": None,
        "fingerprint": fingerprint,
        "generated_at": generated_at,
        "mode": "tournament",
        "phase": view["phase"],
        "kinds": ["group"] if shown_count else [],
        "final_available": False,
        "replays": {"policy": "all", "reveal": "showdown",
                    "max_per_pairing": options.max_hands},
        "teams": len(view["groups"][0]["teams"]) if view["groups"]
        else len(json.loads(files["teams.json"])["teams"]),
    }
    _write_if_changed(manifest_path, _json(manifest))
    return changed


# -- publishing ----------------------------------------------------------


def git_publish(out: str, message: str) -> bool:
    """Commits and pushes out (a git work tree). True if it pushed. A push
    that fails leaves its commit, and the next call pushes it."""

    def git(*args, check=True):
        done = subprocess.run(["git", "-C", out, *args],
                              capture_output=True, text=True, timeout=300)
        if check and done.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: "
                f"{(done.stderr or done.stdout).strip()}")
        return done

    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        raise RuntimeError(f"{out} is not a git work tree")
    git("add", "-A")
    if git("status", "--porcelain").stdout.strip():
        git("commit", "-q", "-m", message)
    ahead = git("rev-list", "--count", "@{u}..HEAD", check=False)
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return False
    git("push", "-q")
    return True


def export_loop(event: Event, args) -> None:
    """The export command: once, or every args.loop seconds."""
    options = Options(
        title=args.title, delay=args.delay, spacing=args.spacing,
        max_hands=args.max_hands,
        # From the command line or the environment, never from a file in
        # the repository: it must not be public before submissions open.
        form_url=args.form_url or os.environ.get("POKER_FORM_URL", ""),
        submissions=args.submissions,
    )
    if args.event_file and os.path.isfile(args.event_file):
        options.event = load_event(args.event_file)
    if args.guide and os.path.isfile(args.guide):
        with open(args.guide, encoding="utf-8-sig") as file:
            options.guide = file.read()
    while True:
        changed = export_site(event, args.out, options)
        stamp = time.strftime("%H:%M:%S")
        print(f"[export {stamp}] {'updated' if changed else 'no change'}")
        if args.git_push and changed:
            try:
                if git_publish(args.out, f"Update results ({stamp})"):
                    print(f"[export {stamp}] pushed to git")
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                print(f"[export {stamp}] push failed: {error}")
        if not args.loop:
            return
        time.sleep(args.loop)

"""The event: uploads, vetting, the round, replays, the site, and submit."""

import json
import os
import shutil
import subprocess
import sys

import pytest

import event_helpers as E
from arena import event, site_export
from arena.event import Event, find_uploads, import_folder
from arena.match import HandRecord, MatchConfig, play_match
from arena.players import Player
from arena.replays import replay_file, select_hands, site_hand
from arena.site_export import Options, export_site, visible_pairings
from card_utils import full_deck
from engine import play_hand


# The front end (site/, event.json) is not in the repository: it is kept by the
# organiser. Tests that need those files are skipped where they are missing.
needs_site = pytest.mark.skipif(
    not os.path.isdir(os.path.join(E.REPO, "site")),
    reason="the site/ front end is not in this checkout")
needs_event_file = pytest.mark.skipif(
    not os.path.isfile(os.path.join(E.REPO, "event.json")),
    reason="event.json is not in this checkout")


def upload(folder, who, team, files, pickle_as=None):
    """Drops one respondent's files the way Google Forms names them."""
    work = folder.parent / f"work-{who.replace(' ', '')}"
    pkl = E.make_team(str(work), files, pickle_as or team)
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(pkl, folder / f"{team} - {who}.pkl")
    for name in files:
        stem, ext = os.path.splitext(name)
        shutil.copyfile(work / name, folder / f"{stem} - {who}{ext}")


@pytest.fixture
def drive(tmp_path):
    return tmp_path / "drive"


@pytest.fixture
def ev(tmp_path):
    return Event(str(tmp_path / "event"))


# -- finding the uploads -------------------------------------------------


def test_google_form_names_group_by_respondent(drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    upload(drive, "Bo Chen", "bravo", {"model.py": E.HELPER_MODEL,
                                       "helper.py": E.HELPER})
    found = {u.team: u for u in find_uploads(str(drive))}
    assert set(found) == {"alpha", "bravo"}
    assert sorted(found["bravo"].names.values()) == ["helper.py", "model.py"]
    assert sorted(found["alpha"].names.values()) == ["model.py"]


def test_one_folder_per_team_also_works(tmp_path):
    for team, source in (("alpha", E.CALL), ("bravo", E.FOLD)):
        E.make_team(str(tmp_path / "in" / team), {"model.py": source}, team)
    found = {u.team: u for u in find_uploads(str(tmp_path / "in"))}
    assert set(found) == {"alpha", "bravo"}
    assert list(found["alpha"].names.values()) == ["model.py"]


def test_a_respondent_with_no_pickle_is_ignored(drive):
    os.makedirs(drive)
    (drive / "model - Ann Lee.py").write_text(E.CALL)
    assert find_uploads(str(drive)) == []


def test_a_respondent_who_uploads_twice_gets_the_newest_pickle(drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    older = drive / "alpha - Ann Lee.pkl"
    newer = drive / "alpha - Ann Lee (1).pkl"
    shutil.copyfile(older, newer)
    os.utime(older, (1, 1))
    found = find_uploads(str(drive))
    assert len(found) == 1 and found[0].pkl == str(newer)


# -- taking them in --------------------------------------------------------


def statuses(ev):
    return {t: (e["latest"]["status"], bool(e["current"]))
            for t, e in ev.teams().items()}


def test_import_vets_then_plays_each_upload(ev, drive, tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    upload(drive, "Bo Chen", "bravo", {"model.py": "import os\n" + E.FOLD})
    upload(drive, "Cy Park", "charlie", {"model.py": E.CRASH})
    evil = E.make_hostile(str(tmp_path / "evil.pkl"))
    os.makedirs(drive, exist_ok=True)
    shutil.copyfile(evil, drive / "evil - Eve Hack.pkl")
    (drive / "model - Eve Hack.py").write_text(E.CALL)
    out = {o.team: o for o in import_folder(ev, str(drive))}
    assert out["alpha"].status == "ready"
    assert out["bravo"].status == "needs_review"
    assert out["charlie"].status == "invalid" and "boom" in out[
        "charlie"].reason
    assert out["evil"].status == "refused"
    assert statuses(ev) == {
        "alpha": ("ready", True), "bravo": ("needs_review", False),
        "charlie": ("invalid", False), "evil": ("refused", False)}
    assert list(ev.ready()) == ["alpha"]
    assert not os.path.exists("pwned.txt")


def test_review_then_approve_lets_flagged_code_play(ev, drive):
    upload(drive, "Bo Chen", "bravo", {"model.py": "import os\n" + E.FOLD})
    import_folder(ev, str(drive))
    assert ev.ready() == {}
    result = event.approve(ev, "bravo")
    assert result.status == "ready"
    assert list(ev.ready()) == ["bravo"]


def test_a_refused_upload_cannot_be_approved(ev, drive, tmp_path):
    evil = E.make_hostile(str(tmp_path / "evil.pkl"))
    os.makedirs(drive)
    shutil.copyfile(evil, drive / "evil - Eve.pkl")
    (drive / "model - Eve.py").write_text(E.CALL)
    import_folder(ev, str(drive))
    with pytest.raises(SystemExit):
        event.approve(ev, "evil")


def test_review_all_makes_every_team_wait(ev, drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    out = import_folder(ev, str(drive), review_all=True)
    assert out[0].status == "needs_review"


def test_importing_the_same_files_again_changes_nothing(ev, drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    again = import_folder(ev, str(drive))
    assert again[0].status == "unchanged"


def test_a_newer_upload_that_fails_never_replaces_a_working_one(ev, drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    first = ev.teams()["alpha"]["current"]
    for name in os.listdir(drive):
        os.remove(drive / name)
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CRASH})
    out = import_folder(ev, str(drive))
    assert out[0].status == "invalid"
    assert ev.teams()["alpha"]["current"] == first
    assert list(ev.ready()) == ["alpha"]


def test_a_newer_good_upload_replaces_the_old_one(ev, drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    first = ev.teams()["alpha"]["current"]
    for name in os.listdir(drive):
        os.remove(drive / name)
    upload(drive, "Ann Lee", "alpha", {"model.py": E.FOLD})
    import_folder(ev, str(drive))
    assert ev.teams()["alpha"]["current"] != first


def test_bad_team_names_are_refused(ev, drive):
    upload(drive, "Ann Lee", "Bad Name!", {"model.py": E.CALL},
           pickle_as="Bad Name!")
    out = import_folder(ev, str(drive))
    assert out[0].status == "refused" and ev.teams() == {}


# -- the replays -----------------------------------------------------------


def play(models, deck_seed=1, stacks=(500, 500), button=0):
    import random

    deck = full_deck()
    random.Random(deck_seed).shuffle(deck)
    result = play_hand(models, deck, list(stacks), button, 10, 20,
                       teams=["a", "b"])
    return HandRecord(0, deck_seed, ["a", "b"], list(stacks), button, 10, 20,
                      result)


class Fixed:
    def __init__(self, *moves):
        self.moves = list(moves)
        self.player_id = None

    def seed(self, hand, player_id):
        self.player_id = player_id

    def run(self, actions, game_state):
        from player_action import PlayerAction

        kind, *amount = self.moves.pop(0) if self.moves else ("call",)
        return getattr(PlayerAction, kind)(self.player_id, *amount)


def test_a_fold_ends_the_hand_with_no_hole_cards_shown():
    record = play([Fixed(("fold",)), Fixed(("call",))])
    hand = site_hand(record, 0)
    assert hand["end"] == "fold" and hand["hole"] is None
    names = [a[1] for a in hand["actions"]]
    assert names[:3] == ["post_small_blind", "post_big_blind", "fold"]
    assert hand["net"] == record.result.net
    assert sum(hand["net"]) == 0


def test_a_showdown_shows_hole_cards_and_hand_names():
    record = play([Fixed(("call",)), Fixed(("call",))])
    hand = site_hand(record, 3)
    assert hand["n"] == 3 and hand["end"] == "showdown"
    assert len(hand["hole"]) == 2 and len(hand["board"]) == 5
    assert any(label for _, label in hand["res"])
    assert sum(w for w, _ in hand["res"]) == 40
    assert [a[3] for a in hand["actions"] if a[1] != "post_big_blind"][-1] \
        == "river"


def test_chips_in_actions_follow_the_replay_rules():
    # Small blind raises to 60, big blind calls: the call adds 40.
    record = play([Fixed(("raise_to", 60)), Fixed(("call",))])
    hand = site_hand(record, 0)
    rows = [(a[0], a[1], a[2]) for a in hand["actions"]]
    assert rows[:4] == [(0, "post_small_blind", 10),
                        (1, "post_big_blind", 20),
                        (0, "raise_to", 60), (1, "call", 40)]
    # then the flop is checked around: checks add nothing
    assert "check" in [a[1] for a in hand["actions"]]


def test_an_opening_bet_is_a_bet_and_all_in_is_a_raise_to_the_stack():
    record = play([Fixed(("call",), ("raise_to", 100)),
                   Fixed(("call",), ("call",))])
    hand = site_hand(record, 0)
    bets = [a for a in hand["actions"] if a[1] == "bet"]
    assert bets and bets[0][2] == 100 and bets[0][3] == "flop"


def test_chips_are_conserved_in_every_replayed_hand():
    import random

    from arena.dummies import DUMMIES

    rng = random.Random(7)
    for k in range(40):
        seats = [DUMMIES["random"](seed=rng.randrange(10**6)),
                 DUMMIES["shove"](seed=1)]
        record = play(seats, deck_seed=k, stacks=(500, 300))
        hand = site_hand(record, k)
        assert sum(hand["net"]) == 0
        assert hand["net"] == record.result.net
        assert len(hand["actions"]) >= 3


def test_the_biggest_swings_are_kept_in_play_order():
    hands = [{"n": i, "bb": 20, "net": [n, -n], "end": "fold"}
             for i, n in enumerate([5, 400, 20, 400, 1])]
    hands[3]["end"] = "showdown"
    chosen = select_hands(hands, 2)
    assert [h["n"] for h in chosen] == [1, 3]
    assert select_hands(hands, 0) == hands
    assert len(select_hands(hands, 99)) == 5
    file = replay_file("group-1", "a", "b", hands, 2)
    assert file["selection"] == "swings" and file["shown"] == 2
    assert file["total_hands"] == 5


# -- the round and the site ---------------------------------------------------


@pytest.fixture
def played(ev, drive):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    upload(drive, "Bo Chen", "bravo", {"model.py": E.SHOVE})
    upload(drive, "Cy Park", "charlie", {"model.py": E.FOLD})
    import_folder(ev, str(drive))
    assert len(ev.ready()) == 3
    count = event.run_round(ev, deals=10, seed=3, concurrency=2,
                            log=lambda *_: None)
    assert count == 3
    return ev


def test_the_round_plays_every_pairing_once_and_resumes(played):
    pairings = event.load_pairings(played)
    assert sorted((p["a"], p["b"]) for p in pairings) == [
        ("alpha", "bravo"), ("alpha", "charlie"), ("bravo", "charlie")]
    assert all(len(p["log"].hands) == 20 for p in pairings)
    again = event.run_round(played, deals=10, seed=3, log=lambda *_: None)
    assert again == 0
    with pytest.raises(SystemExit):
        event.run_round(played, deals=11, seed=3, log=lambda *_: None)


def test_restart_throws_the_old_results_away(played):
    count = event.run_round(played, deals=10, seed=4, restart=True,
                            log=lambda *_: None)
    assert count == 3


def test_every_hand_of_a_pairing_is_kept_for_replays(played):
    from arena.replays import load_hands

    hands = load_hands(played.path("hands", "alpha__bravo.jsonl.gz"))
    assert len(hands) == 20 and hands[0]["n"] == 0


def test_the_delay_and_spacing_decide_what_is_visible():
    pairs = [{"a": "a", "b": str(i), "finished_at": 100.0 + i}
             for i in range(5)]
    assert visible_pairings(pairs, 99, 0, 0) == []
    assert len(visible_pairings(pairs, 102, 0, 0)) == 3
    # a 60 s delay: nothing before 160
    assert visible_pairings(pairs, 159, 60, 0) == []
    # spacing 30: pairings 0..3 are due at 160, 190, 220, 250
    assert len(visible_pairings(pairs, 221, 60, 30)) == 3
    assert [p["n"] for p in visible_pairings(pairs, 10**6, 60, 30)] == [
        1, 2, 3, 4, 5]


def load(out, name):
    with open(os.path.join(out, "data", name), encoding="utf-8") as file:
        return json.load(file)


def test_before_the_round_the_site_lists_the_ready_teams(ev, drive,
                                                         tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    out = str(tmp_path / "site")
    export_site(ev, out, Options())
    view = load(out, "tournament.json")
    assert view["phase"] == "registration" and view["groups"] == []
    assert [t["team"] for t in load(out, "teams.json")["teams"]] == ["alpha"]
    assert load(out, "manifest.json")["kinds"] == []


def test_the_site_reveals_the_round_as_time_passes(played, tmp_path):
    pairs = sorted(event.load_pairings(played),
                   key=lambda p: p["finished_at"])
    start = pairs[0]["finished_at"]
    out = str(tmp_path / "site")
    options = Options(delay=100, spacing=50)

    export_site(played, out, options, now=start + 50)  # nothing yet
    view = load(out, "tournament.json")
    assert view["phase"] == "groups" and view["champion"] is None
    assert view["groups"][0]["board"] is None
    assert not os.path.exists(os.path.join(out, "data", "replays")) or \
        not os.listdir(os.path.join(out, "data", "replays"))

    export_site(played, out, options, now=start + 100 + 50 + 1)  # two shown
    view = load(out, "tournament.json")
    board = view["groups"][0]["board"]
    assert board["pairs_done"] == 2 and board["pairs_total"] == 3
    assert view["champion"] is None
    assert len(load(out, "pairings-group.json")) == 2
    assert len(os.listdir(os.path.join(out, "data", "replays"))) == 2

    export_site(played, out, options, now=start + 10_000)  # all shown
    view = load(out, "tournament.json")
    assert view["phase"] == "done" and view["rounds"] == 1
    board = view["groups"][0]["board"]
    assert board["pairs_done"] == 3
    assert view["champion"] == board["teams"][0]["team"]
    assert board["teams"][0]["rank"] == 1
    assert len(os.listdir(os.path.join(out, "data", "replays"))) == 3
    manifest = load(out, "manifest.json")
    assert manifest["mode"] == "tournament" and manifest["phase"] == "done"


def test_the_exported_data_is_strict_json_and_in_bb_per_100(played,
                                                            tmp_path):
    out = str(tmp_path / "site")
    export_site(played, out, Options())
    for name in ("tournament.json", "teams.json", "pairings-group.json",
                 "manifest.json"):
        with open(os.path.join(out, "data", name), encoding="utf-8") as file:
            json.loads(file.read(), parse_constant=lambda c: pytest.fail(c))
    rows = load(out, "pairings-group.json")
    by = {(r["a"], r["b"]): r for r in rows}
    # always-fold gives away the blinds: calling bot wins about 15 bb/100
    assert by[("alpha", "charlie")]["mean_bb"] > 0
    assert by[("alpha", "charlie")]["verdict"] in ("a", "tie")
    board = load(out, "tournament.json")["groups"][0]["board"]
    shove = next(t for t in board["teams"] if t["team"] == "bravo")
    assert shove["wins"] + shove["draws"] + shove["losses"] == 2


@needs_site
def test_the_export_writes_the_pages_the_guide_and_the_home_text(
        played, tmp_path):
    out = str(tmp_path / "site")
    options = Options(
        event={"tagline": "t", "intro": ["i"], "prize": "p",
               "timeline": [], "rules": [], "contact": ""},
        guide="# Guide\n\ntext\n\n## For the organiser\n\nsecret commands\n",
    )
    export_site(played, out, options)
    assert os.path.exists(os.path.join(out, "index.html"))
    assert os.path.isdir(os.path.join(out, "js"))
    guide = open(os.path.join(out, "data", "submitting.md"),
                 encoding="utf-8").read()
    assert "text" in guide and "secret" not in guide
    assert load(out, "event.json")["prize"] == "p"
    assert load(out, "manifest.json")["has_event"] is True


def test_an_unchanged_export_changes_nothing(played, tmp_path):
    out = str(tmp_path / "site")
    assert export_site(played, out, Options()) is True
    assert export_site(played, out, Options()) is False


def test_no_code_or_pickle_ever_reaches_the_site(played, tmp_path):
    out = str(tmp_path / "site")
    export_site(played, out, Options())
    for current, _, files in os.walk(out):
        for name in files:
            assert not name.endswith((".pkl", ".py")), name
    text = "".join(
        open(os.path.join(c, f), encoding="utf-8", errors="ignore").read()
        for c, _, fs in os.walk(os.path.join(out, "data")) for f in fs)
    assert "class Model" not in text


def test_the_event_file_is_tidied(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({
        "tagline": " Go ", "intro": ["a", 3, ""], "prize": "300",
        "timeline": [{"time": "12:00", "title": "S", "detail": "d", "x": 1},
                     {"time": "13"}, "junk"],
        "rules": "no", "unknown": "dropped"}))
    assert site_export.load_event(str(path)) == {
        "tagline": "Go", "intro": ["a"], "prize": "300",
        "timeline": [{"time": "12:00", "title": "S", "detail": "d"}],
        "rules": [], "contact": "", "repo": "", "whatsapp": ""}
    path.write_text("[1]")
    with pytest.raises(SystemExit):
        site_export.load_event(str(path))
    with pytest.raises(SystemExit):
        site_export.load_event(str(tmp_path / "missing.json"))


@needs_event_file
def test_the_repositorys_event_file_loads():
    found = site_export.load_event(os.path.join(E.REPO, "event.json"))
    assert found["timeline"] and found["intro"] and found["prize"]
    # submissions open at 16:00 and close at 18:00: nothing says 4pm closes
    times = [row["time"] for row in found["timeline"]]
    assert times[:4] == ["12:00", "16:00", "18:00", "After 18:00"]


# -- the command line --------------------------------------------------------


def run_cli(args, cwd=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd or E.REPO,
                          capture_output=True, text=True, env=env,
                          timeout=300)


@needs_event_file
def test_the_event_command_line_end_to_end(drive, tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    upload(drive, "Bo Chen", "bravo", {"model.py": "import os\n" + E.SHOVE})
    root = str(tmp_path / "event")
    done = run_cli(["arena.event", "--root", root, "import", str(drive)])
    assert done.returncode == 0, done.stderr
    assert "READY" in done.stdout and "NEEDS_REVIEW" in done.stdout
    done = run_cli(["arena.event", "--root", root, "review", "bravo"])
    assert "imports os" in done.stdout and "class Model" in done.stdout
    done = run_cli(["arena.event", "--root", root, "approve", "bravo"])
    assert "ready" in done.stdout
    done = run_cli(["arena.event", "--root", root, "run", "--deals", "5",
                    "--concurrency", "1"])
    assert done.returncode == 0, done.stderr
    assert "1/1" in done.stdout
    done = run_cli(["arena.event", "--root", root, "status"])
    assert "1/1 pairings" in done.stdout and "finished" in done.stdout
    out = str(tmp_path / "site")
    done = run_cli(["arena.event", "--root", root, "export", "--out", out,
                    "--event-file", os.path.join(E.REPO, "event.json")])
    assert done.returncode == 0, done.stderr
    assert load(out, "tournament.json")["phase"] == "done"


# -- the team's tool -----------------------------------------------------------


def test_submit_makes_the_pickle_and_lists_the_files_to_upload(tmp_path):
    work = tmp_path / "work"
    os.makedirs(work)
    E.make_team(str(work), {"model.py": E.HELPER_MODEL,
                            "helper.py": E.HELPER})
    os.remove(work / "team.pkl")
    env = dict(os.environ, PYTHONPATH=E.REPO, PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run(
        [sys.executable, "-m", "submit", "--team", "myteam", "--out",
         str(work)], cwd=work, env=env, capture_output=True, text=True,
        timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (work / "myteam.pkl").exists()
    listed = done.stdout.split("Upload these files")[1]
    assert "myteam.pkl" in listed and "model.py" in listed
    assert "helper.py" in listed
    assert "card_utils" not in listed and "player_action" not in listed


@pytest.mark.parametrize("bad", ["Bad Name", "", "x" * 40, "-no"])
def test_submit_checks_the_team_name(tmp_path, bad):
    env = dict(os.environ, PYTHONPATH=E.REPO)
    done = subprocess.run(
        [sys.executable, "-m", "submit", f"--team={bad}"], cwd=tmp_path,
        env=env, capture_output=True, text=True)
    assert done.returncode == 1
    assert "team name" in done.stdout


def test_a_match_hook_sees_every_hand_and_changes_nothing():
    players = [Player("a", "dummy:call"), Player("b", "dummy:fold")]
    seen = []
    plain = play_match(players, MatchConfig(deals=3, seed=1))
    hooked = play_match(players, MatchConfig(deals=3, seed=1), seen.append)
    assert [h.net for h in plain.hands] == [h.net for h in hooked.hands]
    assert len(seen) == len(hooked.hands) == 6
    assert seen[0].seats == hooked.hands[0].seats
    assert seen[0].button == 0 and seen[0].stacks == [500, 500]


# -- submissions: the form link is released only when the organiser says --

FORM = "https://forms.example/secret-form-link"
EVENT = {"submissions": {"opens": "16:00", "closes": "18:00"}}


def test_the_form_link_exists_only_while_submissions_are_open():
    state = site_export.submission_state
    closed = state(EVENT, FORM, "closed")
    assert closed["status"] == "closed" and closed["why"] == "not_yet"
    assert closed["form_url"] is None and closed["opens"] == "16:00"
    opened = state(EVENT, FORM, "open")
    assert opened["status"] == "open" and opened["form_url"] == FORM
    assert opened["closes"] == "18:00" and opened["why"] is None
    over = state(EVENT, FORM, "over")
    assert over["status"] == "closed" and over["why"] == "over"
    assert over["form_url"] is None
    assert state({}, FORM)["status"] == "closed"  # closed unless told
    assert state(EVENT, "javascript:alert(1)", "open")["form_url"] is None
    assert state(EVENT, "", "open")["form_url"] is None


def event_with_window(tmp_path):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({**EVENT, "intro": ["x"],
                                "repo": "https://example.org/code"}))
    return site_export.load_event(str(path))


def published_text(out):
    text = ""
    for current, _, files in os.walk(out):
        for name in files:
            if name.endswith((".json", ".md", ".html", ".js")):
                with open(os.path.join(current, name), encoding="utf-8",
                          errors="ignore") as file:
                    text += file.read()
    return text


@pytest.mark.parametrize("say, secret_visible, status", [
    ("closed", False, "closed"), ("open", True, "open"),
    ("over", False, "closed")])
def test_the_site_carries_the_form_link_only_when_told_open(
        ev, drive, tmp_path, say, secret_visible, status):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    out = str(tmp_path / "site")
    options = Options(event=event_with_window(tmp_path), form_url=FORM,
                      submissions=say)
    export_site(ev, out, options)
    assert load(out, "manifest.json")["submissions"]["status"] == status
    assert (FORM in published_text(out)) is secret_visible
    assert load(out, "event.json")["repo"] == "https://example.org/code"


def test_the_site_is_closed_by_default_even_with_the_link_given(
        ev, drive, tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    out = str(tmp_path / "site")
    export_site(ev, out, Options(event=event_with_window(tmp_path),
                                 form_url=FORM))
    assert FORM not in published_text(out)
    assert load(out, "manifest.json")["submissions"]["status"] == "closed"


def test_the_link_leaves_the_data_again_when_submissions_close(
        ev, drive, tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    import_folder(ev, str(drive))
    out = str(tmp_path / "site")
    event = event_with_window(tmp_path)
    export_site(ev, out, Options(event=event, form_url=FORM,
                                 submissions="open"))
    assert FORM in published_text(out)
    export_site(ev, out, Options(event=event, form_url=FORM,
                                 submissions="over"))
    assert FORM not in published_text(out)


@needs_event_file
def test_the_repositorys_event_file_never_holds_the_form_link():
    path = os.path.join(E.REPO, "event.json")
    with open(path, encoding="utf-8") as file:
        text = file.read()
    assert "forms.gle" not in text and "docs.google.com/forms" not in text
    found = site_export.load_event(path)
    assert found["repo"] == "https://github.com/Manchester-AI-Society/poker_bot"
    assert found["whatsapp"] == "https://tr.ee/5W_MS_DJK9"
    assert found["submissions"] == {"opens": "16:00", "closes": "18:00"}


def test_the_event_file_reads_the_window_text_and_the_repo(tmp_path):
    found = event_with_window(tmp_path)
    assert found["repo"] == "https://example.org/code"
    assert found["submissions"] == {"opens": "16:00", "closes": "18:00"}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"repo": "http://plain.example"}))
    assert site_export.load_event(str(bad))["repo"] == ""
    assert "submissions" not in site_export.load_event(str(bad))


@needs_event_file
def test_the_command_line_defaults_to_closed_and_takes_the_link_from_env(
        drive, tmp_path):
    upload(drive, "Ann Lee", "alpha", {"model.py": E.CALL})
    root = str(tmp_path / "event")
    run_cli(["arena.event", "--root", root, "import", str(drive)])
    out = str(tmp_path / "site")
    env = dict(os.environ, POKER_FORM_URL=FORM, PYTHONDONTWRITEBYTECODE="1")

    def export(*extra):
        return subprocess.run(
            [sys.executable, "-m", "arena.event", "--root", root, "export",
             "--out", out, "--event-file", os.path.join(E.REPO, "event.json"),
             *extra], cwd=E.REPO, capture_output=True, text=True, env=env)

    assert export().returncode == 0
    assert FORM not in published_text(out)
    assert export("--submissions", "open").returncode == 0
    assert FORM in published_text(out)
    assert export("--submissions", "over").returncode == 0
    assert FORM not in published_text(out)

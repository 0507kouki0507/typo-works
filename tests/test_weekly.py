"""Weekly batch maker. Claude, git, gh and Instagram are all fakes; nothing touches the network."""

import json
import os
import stat
import subprocess
from functools import partial
from pathlib import Path

import pytest

from typo import weekly
from typo.catalog import Work, load_catalog, save_catalog
from typo.checks import MAX_JA_CHARS, MAX_LINE_CHARS, MAX_LINES
from typo.instagram import InstagramError
from typo.weekly import AUTO_APPROVE_AFTER, BATCH, TRADEMARK_HOLD, Rights, make_batch, propose_phrases, rights_check

TODAY = "2026-11-23"

GOOD = [
    ("SLOW\nIS\nFINE", "ゆっくりでいい。"),
    ("REST\nFIRST", "まず、休む。"),
    ("ENOUGH\nFOR\nTODAY", "今日はここまで。"),
    ("HALF\nDONE\nIS OK", "半分でいい。"),
    ("QUIET\nWINS", "静かな勝ち。"),
    ("NOT\nTODAY", "今日はやめとく。"),
    ("SOFT\nBUT\nSURE", "やわらかく、確かに。"),
    ("LESS\nIS\nPLENTY", "少なくて十分。"),
    ("TAKE\nA\nNAP", "昼寝しよう。"),
    ("NO\nRUSH", "急がない。"),
    ("CALM\nMODE", "おだやかに。"),
    ("LOW\nKEY", "控えめに。"),
    ("BREATHE\nOUT", "息をはく。"),
    ("STAY\nSMALL", "小さくいよう。"),
]


def candidate(phrase: str, ja: str, layout: str = "stack", accent_line: int = 0) -> dict:
    return {"phrase": phrase, "ja": ja, "layout": layout, "accent_line": accent_line}


def reply(candidates: list[dict]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


class FakeClaude:
    """Answers the three prompts. `rights_ng` / `looks_ng` hold phrases (one line, spaces) to reject;
    `jplatpat_unreadable` holds phrases whose web search is clean but J-PlatPat could not be read."""

    def __init__(
        self,
        proposals: list[str],
        rights_ng: set[str] = frozenset(),
        looks_ng: set[str] = frozenset(),
        jplatpat_unreadable: set[str] = frozenset(),
    ) -> None:
        self.proposals = list(proposals)
        self.rights_ng = rights_ng
        self.looks_ng = looks_ng
        self.jplatpat_unreadable = jplatpat_unreadable
        self.prompts: list[str] = []
        self.rights_asked: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "J-PlatPat" in prompt:
            phrase = prompt.split("「", 1)[1].split("」", 1)[0]
            self.rights_asked.append(phrase)
            jplatpat = "unreadable" if phrase in self.jplatpat_unreadable else "clear"
            return json.dumps({"ok": phrase not in self.rights_ng, "jplatpat": jplatpat, "reason": "test"})
        if "post.jpg" in prompt:
            ng = any(f"英語（大きな文字）: {' / '.join(p.split())}" in prompt for p in self.looks_ng)
            return json.dumps({"ok": not ng, "reason": "test"})
        return self.proposals.pop(0)


def good_candidates(count: int) -> list[dict]:
    return [candidate(p, j) for p, j in GOOD[:count]]


def approved_works(count: int) -> list[Work]:
    return [
        Work(no=i, phrase=f"OLD\nWORK {i}", ja="むかし", layout="stack", accent_line=-1, created="2026-11-01", approved=True)
        for i in range(1, count + 1)
    ]


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    works_dir = tmp_path / "works"
    works_dir.mkdir()
    catalog = works_dir / "catalog.json"
    save_catalog(catalog, [])
    return catalog, works_dir


def test_prompt_states_the_check_limits_and_layouts() -> None:
    run = FakeClaude([reply(good_candidates(2))])
    propose_phrases(run, [Work(1, "MAKE\nLESS\nNOISE", "静かに、強く。", "stack", 2, "2026-11-01")], 2)
    prompt = run.prompts[0]
    assert "がんばりすぎない人の本音" in prompt
    assert f"1〜{MAX_LINES}行" in prompt and f"{MAX_LINE_CHARS}文字以内" in prompt and f"{MAX_JA_CHARS}文字以内" in prompt
    assert all(name in prompt for name in ("stack", "giant", "diagonal", "invert"))
    assert "MAKE LESS NOISE" in prompt
    assert "曲名" in prompt and "定番フレーズ" in prompt


def test_batch_adds_passing_works(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    save_catalog(catalog, approved_works(2))
    run = FakeClaude([reply(good_candidates(BATCH * 2))])
    added = make_batch(run, catalog, works_dir, TODAY)
    assert [w.no for w in added] == [3, 4, 5, 6, 7, 8, 9]
    assert all(w.created == TODAY for w in added)
    assert all((works_dir / f"{w.no:03d}" / "post.jpg").is_file() for w in added)
    assert [w.no for w in load_catalog(catalog)] == list(range(1, 10))
    assert "\"candidates\"" in run.prompts[0] and f"{BATCH * 2}本" in run.prompts[0]


def test_banned_and_duplicate_candidates_dropped(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    save_catalog(catalog, [Work(1, "MAKE\nLESS\nNOISE", "静かに、強く。", "stack", 2, "2026-11-01")])
    bad = [
        candidate("JUST\nDO IT", "やるだけ。"),
        candidate("make less\nnoise", "静かに。"),
        candidate("SLOW\nIS\nFINE", "二回目。"),  # duplicates the first good one within the batch
        candidate("WAY TOO LONG LINE", "長い。"),
    ]
    run = FakeClaude([reply([candidate(*GOOD[0]), *bad, candidate(*GOOD[1])])])
    added = make_batch(run, catalog, works_dir, TODAY, n=7)
    assert [w.phrase for w in added] == [GOOD[0][0], GOOD[1][0]]
    assert [w.no for w in added] == [2, 3]
    assert "JUST DO IT" not in run.rights_asked and "WAY TOO LONG LINE" not in run.rights_asked


def test_rights_ng_dropped(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    run = FakeClaude([reply(good_candidates(3))], rights_ng={"REST FIRST"})
    added = make_batch(run, catalog, works_dir, TODAY)
    assert [w.phrase for w in added] == [GOOD[0][0], GOOD[2][0]]
    assert [w.no for w in added] == [1, 2]
    assert sorted(p.name for p in works_dir.iterdir()) == ["001", "002", "catalog.json"]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"ok": True, "jplatpat": "clear", "reason": "0件"}, Rights.CLEAN),
        ({"ok": True, "jplatpat": "unreadable", "reason": "開けない"}, Rights.PENDING),
        ({"ok": False, "jplatpat": "clear", "reason": "曲名"}, Rights.REJECTED),
        ({"ok": False, "jplatpat": "found", "reason": "同一商標"}, Rights.REJECTED),
        ({"ok": True, "jplatpat": "found", "reason": "矛盾"}, Rights.REJECTED),
        ({"ok": True, "reason": "jplatpatなし"}, Rights.REJECTED),
    ],
)
def test_rights_three_outcomes(answer: dict, expected: Rights, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weekly, "TRUST_JPLATPAT_CLEAR", True)
    assert rights_check(lambda prompt: json.dumps(answer, ensure_ascii=False), "SLOW\nIS\nFINE")[0] is expected
    assert weekly.rights_ok(lambda prompt: json.dumps(answer), "SLOW\nIS\nFINE") is (expected is Rights.CLEAN)


def test_rights_check_that_cannot_run_is_rejected() -> None:
    def broken(prompt: str) -> str:
        raise RuntimeError("claude -p failed")

    assert rights_check(broken, "SLOW\nIS\nFINE")[0] is Rights.REJECTED


def test_clear_jplatpat_is_held_until_trusted() -> None:
    answer = json.dumps({"ok": True, "jplatpat": "clear", "reason": "0件"})
    assert weekly.TRUST_JPLATPAT_CLEAR is False
    assert rights_check(lambda prompt: answer, "SLOW\nIS\nFINE")[0] is Rights.PENDING
    assert weekly.rights_ok(lambda prompt: answer, "SLOW\nIS\nFINE") is False


@pytest.mark.parametrize("trust", [False, True])
def test_pending_trademark_added_on_hold(paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, trust: bool) -> None:
    monkeypatch.setattr(weekly, "TRUST_JPLATPAT_CLEAR", trust)
    catalog, works_dir = paths
    run = FakeClaude([reply(good_candidates(4))], jplatpat_unreadable={"SLOW IS FINE"})
    added = make_batch(run, catalog, works_dir, TODAY, n=4)
    clear_hold = None if trust else TRADEMARK_HOLD
    assert [w.hold for w in added] == [TRADEMARK_HOLD, clear_hold, clear_hold, clear_hold]
    assert all((works_dir / f"{w.no:03d}" / "post.jpg").is_file() for w in added)
    assert [w.hold for w in load_catalog(catalog)] == [w.hold for w in added]


def test_batch_adds_at_most_n_including_held(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    run = FakeClaude([reply(good_candidates(6))], jplatpat_unreadable={"SLOW IS FINE", "REST FIRST"})
    added = make_batch(run, catalog, works_dir, TODAY, n=2)
    assert [(w.no, w.hold) for w in added] == [(1, TRADEMARK_HOLD), (2, TRADEMARK_HOLD)]
    assert len(load_catalog(catalog)) == 2


def test_rights_unreadable_reply_is_not_ok() -> None:
    assert weekly.rights_ok(lambda prompt: "I could not search, sorry", "SLOW\nIS\nFINE") is False
    assert weekly.rights_ok(lambda prompt: '{"ok": "yes", "reason": "x"}', "SLOW\nIS\nFINE") is False


def test_looks_ng_dropped_and_no_file_left(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    run = FakeClaude([reply(good_candidates(3))], looks_ng={"SLOW IS FINE"})
    added = make_batch(run, catalog, works_dir, TODAY)
    assert [w.phrase for w in added] == [GOOD[1][0], GOOD[2][0]]
    assert [w.no for w in added] == [1, 2]
    assert sorted(p.name for p in works_dir.iterdir()) == ["001", "002", "catalog.json"]
    assert [w.phrase for w in load_catalog(catalog)] == [GOOD[1][0], GOOD[2][0]]


def test_looks_unreadable_reply_is_not_ok(tmp_path: Path) -> None:
    assert weekly.looks_ok(lambda prompt: "looks fine to me", tmp_path / "post.jpg") is False


def test_short_batch_is_ok(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    run = FakeClaude([reply(good_candidates(3))])
    added = make_batch(run, catalog, works_dir, TODAY)
    assert len(added) == 3
    assert len(load_catalog(catalog)) == 3
    assert len(run.prompts) == 1 + 3 + 3  # one proposal, no second round to fill the batch


def test_first_batch_unapproved_then_auto(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    first = make_batch(FakeClaude([reply(good_candidates(2))]), catalog, works_dir, TODAY)
    assert [w.approved for w in first] == [False, False]

    save_catalog(catalog, approved_works(AUTO_APPROVE_AFTER))
    later = make_batch(FakeClaude([reply(good_candidates(2))]), catalog, works_dir, TODAY)
    assert [w.approved for w in later] == [True, True]


def test_bad_json_retries_once(paths: tuple[Path, Path]) -> None:
    catalog, works_dir = paths
    run = FakeClaude(["Sure! Here are some ideas: ...", reply(good_candidates(1))])
    assert len(make_batch(run, catalog, works_dir, TODAY)) == 1

    run = FakeClaude(["not json", '{"candidates": "nope"}', reply(good_candidates(1))])
    with pytest.raises(weekly.ClaudeReplyError):
        propose_phrases(run, [], 2)
    assert len(run.prompts) == 2


def test_malformed_candidates_are_skipped() -> None:
    items = [
        candidate(*GOOD[0], layout="spiral"),
        candidate(*GOOD[1], accent_line=5),
        {"phrase": "NO JA"},
        candidate(*GOOD[2]),
    ]
    assert propose_phrases(FakeClaude([reply(items)]), [], 4) == [candidate(*GOOD[2])]


# --- main(): git and token refresh with fakes -------------------------------------------------

OLD_TOKEN = "old-token-for-tests"
NEW_TOKEN = "new-token-for-tests"


class FakeShell:
    """Records commands. A command starting with a key of `fail` fails; the value caps how many
    times (None = always). A plain string means that prefix always fails."""

    def __init__(self, fail: str | dict[str, int | None] | None = None, events: list[str] | None = None) -> None:
        self.fail = {fail: None} if isinstance(fail, str) else dict(fail or {})
        self.events = events if events is not None else []
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], input: str | None = None) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, input))
        self.events.append(" ".join(args[:2]))
        code = 0
        for prefix, left in self.fail.items():
            if " ".join(args).startswith(prefix) and left != 0:
                code = 1
                self.fail[prefix] = None if left is None else left - 1
        return subprocess.CompletedProcess(args, code, stdout="", stderr="boom" if code else "")

    def commands(self) -> list[str]:
        return [" ".join(args[:2]) for args, _ in self.calls]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "works").mkdir()
    save_catalog(tmp_path / "works" / "catalog.json", [])
    env = tmp_path / ".env"
    env.write_text(f"IG_ACCESS_TOKEN={OLD_TOKEN}\nIG_USER_ID=123\nSUZURI_API_KEY=abc\n", encoding="utf-8")
    env.chmod(0o600)
    return tmp_path


def no_record(token: str, root: Path, today: str) -> None:
    return None


def run_main(root: Path, shell: FakeShell, refresh, candidates: int = 2, record=no_record) -> int:  # type: ignore[no-untyped-def]
    run = FakeClaude([reply(good_candidates(candidates))])
    return weekly.main(root=root, run=run, shell=shell, refresh=refresh, record=record, today=TODAY)


def test_main_commits_only_new_works_and_refreshes_token(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeShell()
    assert run_main(root, shell, lambda token: NEW_TOKEN) == 0
    pull, add, commit, push, secret = shell.calls
    paths = ["works/catalog.json", "works/001", "works/002"]
    assert pull[0] == ["git", "pull", "--ff-only"]
    assert add[0] == ["git", "add", "--", *paths]
    assert commit[0][:2] == ["git", "commit"] and commit[0][-4:] == ["--", *paths]
    assert push[0] == ["git", "push"]
    assert secret == (["gh", "secret", "set", "IG_ACCESS_TOKEN"], NEW_TOKEN)
    assert all(NEW_TOKEN not in arg and OLD_TOKEN not in arg for args, _ in shell.calls for arg in args)
    env = root / ".env"
    assert env.read_text(encoding="utf-8") == f"IG_ACCESS_TOKEN={NEW_TOKEN}\nIG_USER_ID=123\nSUZURI_API_KEY=abc\n"
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    out = capsys.readouterr()
    assert NEW_TOKEN not in out.out + out.err and OLD_TOKEN not in out.out + out.err


def test_main_without_new_works_skips_git(root: Path) -> None:
    shell = FakeShell()
    assert run_main(root, shell, lambda token: NEW_TOKEN, candidates=0) == 0
    assert shell.commands() == ["git pull", "gh secret"]


def test_main_refresh_failure_keeps_old_token(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def refresh(token: str) -> str:
        raise InstagramError("Instagram GET /refresh_access_token failed: HTTP 400, Graph error code 190", code=190)

    shell = FakeShell()
    assert run_main(root, shell, refresh) == 1
    assert shell.commands() == ["git pull", "git add", "git commit", "git push"]
    assert f"IG_ACCESS_TOKEN={OLD_TOKEN}\n" in (root / ".env").read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert "token refresh failed" in err and OLD_TOKEN not in err


def test_main_unexpected_refresh_error_hides_its_text(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def refresh(token: str) -> str:
        raise RuntimeError(f"leaked {token}")

    assert run_main(root, FakeShell(), refresh) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and OLD_TOKEN not in err


def test_main_push_failure_still_refreshes(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeShell(fail="git push")
    assert run_main(root, shell, lambda token: NEW_TOKEN) == 1
    assert shell.commands() == ["git pull", "git add", "git commit", "git push", "git pull", "git push", "gh secret"]
    assert f"IG_ACCESS_TOKEN={NEW_TOKEN}\n" in (root / ".env").read_text(encoding="utf-8")
    assert "git push failed" in capsys.readouterr().err


def test_main_pulls_before_making_anything(root: Path) -> None:
    events: list[str] = []
    shell = FakeShell(events=events)
    claude = FakeClaude([reply(good_candidates(1))])

    def run(prompt: str) -> str:
        events.append("claude")
        return claude(prompt)

    assert weekly.main(root=root, run=run, shell=shell, refresh=lambda token: NEW_TOKEN, record=no_record, today=TODAY) == 0
    assert events[0] == "git pull" and events.index("claude") > 0


def test_main_pull_failure_makes_nothing(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeShell(fail="git pull --ff-only")
    run = FakeClaude([reply(good_candidates(2))])
    assert weekly.main(root=root, run=run, shell=shell, refresh=lambda token: NEW_TOKEN, record=no_record, today=TODAY) == 1
    assert run.prompts == []
    assert load_catalog(root / "works" / "catalog.json") == []
    assert shell.commands() == ["git pull", "gh secret"]
    assert "batch skipped" in capsys.readouterr().err


def test_push_rejected_rebases_once_then_pushes(root: Path) -> None:
    shell = FakeShell(fail={"git push": 1})
    assert run_main(root, shell, lambda token: NEW_TOKEN) == 0
    assert [args for args, _ in shell.calls][3:6] == [["git", "push"], ["git", "pull", "--rebase", "--autostash"], ["git", "push"]]


def test_push_rejected_and_rebase_failing_is_aborted_and_reported(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeShell(fail={"git push": 1, "git pull --rebase": None})
    assert run_main(root, shell, lambda token: NEW_TOKEN) == 1
    assert shell.commands()[3:] == ["git push", "git pull", "git rebase", "gh secret"]
    assert shell.calls[5][0] == ["git", "rebase", "--abort"]
    assert "push rejected and rebase failed" in capsys.readouterr().err


def test_main_secret_failure_is_reported(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main(root, FakeShell(fail="gh secret"), lambda token: NEW_TOKEN) == 1
    assert "gh secret set failed" in capsys.readouterr().err


def test_main_batch_failure_still_refreshes(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shell = FakeShell()
    run = FakeClaude(["garbage", "garbage"])
    assert weekly.main(root=root, run=run, shell=shell, refresh=lambda token: NEW_TOKEN, record=no_record, today=TODAY) == 1
    assert shell.commands() == ["git pull", "gh secret"]
    assert "batch failed" in capsys.readouterr().err


def test_main_commits_works_added_before_a_crash(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    real_save = weekly.save_post_jpeg
    calls = []

    def save_then_crash(work: Work, path: Path) -> None:
        calls.append(work.no)
        if len(calls) == 2:
            raise OSError("disk full")
        real_save(work, path)

    monkeypatch.setattr(weekly, "save_post_jpeg", save_then_crash)
    shell = FakeShell()
    assert run_main(root, shell, lambda token: NEW_TOKEN, candidates=3) == 1
    assert shell.calls[1][0] == ["git", "add", "--", "works/catalog.json", "works/001"]
    assert shell.commands() == ["git pull", "git add", "git commit", "git push", "gh secret"]
    assert [w.no for w in load_catalog(root / "works" / "catalog.json")] == [1]
    assert (root / "works" / "001" / "post.jpg").is_file()
    assert not (root / "works" / "002" / "post.jpg").exists()
    assert "batch failed: OSError" in capsys.readouterr().err


def test_main_records_weekly_numbers_with_refreshed_token(root: Path) -> None:
    seen: list[tuple[str, Path, str]] = []
    assert run_main(root, FakeShell(), lambda token: NEW_TOKEN, record=lambda *args: seen.append(args)) == 0
    assert seen == [(NEW_TOKEN, root, TODAY)]


def test_main_metrics_failure_is_reported_and_exits_nonzero(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def record(token: str, root: Path, today: str) -> None:
        raise InstagramError("stats failed")

    shell = FakeShell()
    assert run_main(root, shell, lambda token: NEW_TOKEN, record=record) == 1
    assert shell.commands()[-1] == "gh secret"
    assert "weekly numbers failed" in capsys.readouterr().err


def test_main_refresh_failure_still_records_numbers(root: Path) -> None:
    def refresh(token: str) -> str:
        raise InstagramError("nope", code=190)

    seen: list[str] = []
    assert run_main(root, FakeShell(), refresh, record=lambda token, *rest: seen.append(token)) == 1
    assert seen == [OLD_TOKEN]


def test_claude_child_env_has_no_dotenv_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("IG_ACCESS_TOKEN", "IG_USER_ID", "SUZURI_API_KEY"):
        monkeypatch.setenv(name, "fake-secret-for-tests")
    seen: dict = {}

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(weekly.subprocess, "run", fake_run)
    assert weekly.claude_cli(tmp_path)("prompt") == '{"ok": true}'
    env = seen["env"]
    assert not {"IG_ACCESS_TOKEN", "IG_USER_ID", "SUZURI_API_KEY"} & env.keys()
    assert env["PATH"] == os.environ["PATH"] and env["HOME"] == os.environ["HOME"]
    assert "fake-secret-for-tests" not in " ".join(env.values())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _clone(remote: Path, path: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True, capture_output=True)
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")
    return path


def test_publish_with_real_git_rebases_over_daily_push_and_keeps_other_staged_files_out(tmp_path: Path) -> None:
    """Local bare repo only. The 'daily' clone pushes first, so the weekly push is rejected once."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _clone(remote, tmp_path / "seed")
    (seed / "works").mkdir()
    save_catalog(seed / "works" / "catalog.json", approved_works(1))
    (seed / "README.md").write_text("readme\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-qm", "seed")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    weekly_repo = _clone(remote, tmp_path / "weekly")
    daily = _clone(remote, tmp_path / "daily")

    shell = partial(weekly._shell, cwd=weekly_repo)
    assert weekly.sync_with_remote(shell) is None
    catalog = weekly_repo / "works" / "catalog.json"
    new = Work(2, "SLOW\nIS\nFINE", "ゆっくりでいい。", "stack", 0, TODAY)
    save_catalog(catalog, [*load_catalog(catalog), new])
    (weekly_repo / "works" / "002").mkdir()
    (weekly_repo / "works" / "002" / "post.jpg").write_bytes(b"jpg")
    (weekly_repo / "README.md").write_text("someone else's staged edit\n", encoding="utf-8")
    _git(weekly_repo, "add", "README.md")

    (daily / "notes.txt").write_text("daily\n", encoding="utf-8")
    _git(daily, "add", "notes.txt")
    _git(daily, "commit", "-qm", "daily")
    _git(daily, "push", "-q", "origin", "HEAD:main")

    assert weekly.publish_batch(shell, [new]) is None
    log = _git(remote, "log", "--format=%s", "main").split("\n")
    assert log[:3] == ["feat: add works NO.002-NO.002", "daily", "seed"]
    assert _git(remote, "show", "--name-only", "--format=", "main").split() == ["works/002/post.jpg", "works/catalog.json"]
    # the other edit was not committed; --autostash puts it back as a working-tree change
    assert "README.md" in _git(weekly_repo, "status", "--porcelain")
    assert (weekly_repo / "README.md").read_text(encoding="utf-8") == "someone else's staged edit\n"


def test_user_id_prefers_environment_then_reads_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("IG_ACCESS_TOKEN=tok\nIG_USER_ID=from file\n", encoding="utf-8")
    monkeypatch.delenv("IG_USER_ID", raising=False)
    assert weekly._user_id(env) == "from file"  # spaces are fine: nothing is shell-sourced
    monkeypatch.setenv("IG_USER_ID", "from env")
    assert weekly._user_id(env) == "from env"
    monkeypatch.delenv("IG_USER_ID")
    assert weekly._user_id(tmp_path / "missing.env") == ""

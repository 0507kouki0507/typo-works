from pathlib import Path

import pytest

from typo import hold
from typo.catalog import Work, load_catalog, next_pending, save_catalog, stock_count
from typo.weekly import TRADEMARK_HOLD


def make(no: int, hold_reason: str | None = None, phrase: str = "MAKE\nLESS", approved: bool = True) -> Work:
    return Work(
        no=no, phrase=phrase, ja=f"日本語{no}", layout="stack", accent_line=0,
        created="2026-11-01", approved=approved, hold=hold_reason,
    )


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "catalog.json"
    save_catalog(p, [
        make(1),
        make(8, TRADEMARK_HOLD, "MAKE\nLESS\nNOISE"),
        make(9, TRADEMARK_HOLD, "STAY\nSMALL"),
        make(10, TRADEMARK_HOLD, "BE\nHERE"),
        make(11, "suzuri_poster: RuntimeError, listing may exist on SUZURI"),
    ])
    return p


def holds(p: Path) -> dict[int, str | None]:
    return {w.no: w.hold for w in load_catalog(p)}


def test_list_order_and_format(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main(["list"], path) == 0
    out = capsys.readouterr().out
    assert "NO.008  MAKE / LESS / NOISE  日本語8  trademark check pending" in out
    assert out.index("NO.008") < out.index("NO.009") < out.index("NO.010") < out.index("NO.011")
    assert out.index("NO.010") < out.index("その他の保留") < out.index("NO.011")
    assert "NO.001" not in out
    assert "4件" in out


def test_no_arguments_lists(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main([], path) == 0
    assert "NO.008" in capsys.readouterr().out


def test_list_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "c.json"
    save_catalog(p, [make(1)])
    assert hold.main(["list"], p) == 0
    assert "保留中の作品はありません" in capsys.readouterr().out


def test_clear_happy_path(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main(["clear", "8", "9"], path) == 0
    assert holds(path)[8] is None and holds(path)[9] is None
    assert holds(path)[10] == TRADEMARK_HOLD
    assert "NO.008" in capsys.readouterr().out


def test_clear_refuses_other_hold(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = path.read_bytes()
    assert hold.main(["clear", "11"], path) == 1
    assert path.read_bytes() == before
    assert "NO.011" in capsys.readouterr().out


def test_clear_mixed(path: Path) -> None:
    assert hold.main(["clear", "8", "11", "9"], path) == 1
    h = holds(path)
    assert h[8] is None and h[9] is None and h[11] is not None


def test_unknown_and_not_held(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main(["clear", "99", "1", "8"], path) == 1
    out = capsys.readouterr().out
    assert "NO.099" in out and "NO.001" in out
    assert holds(path)[8] is None


def test_unchanged_catalog_not_rewritten(path: Path) -> None:
    before, mtime = path.read_bytes(), path.stat().st_mtime_ns
    hold.main(["clear", "99", "11"], path)
    hold.main(["reject", "1"], path)
    hold.main(["list"], path)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == mtime


def test_reject(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main(["reject", "9"], path) == 0
    assert holds(path)[9] == hold.REJECTED_HOLD
    capsys.readouterr()
    hold.main(["list"], path)
    out = capsys.readouterr().out
    assert out.index("その他の保留") < out.index("NO.009")


def test_reject_refuses_other_hold(path: Path) -> None:
    assert hold.main(["reject", "11", "8"], path) == 1
    assert holds(path)[11] != hold.REJECTED_HOLD and holds(path)[8] == hold.REJECTED_HOLD


def test_rejected_not_clearable(path: Path) -> None:
    hold.main(["reject", "9"], path)
    assert hold.main(["clear", "9"], path) == 1
    assert holds(path)[9] == hold.REJECTED_HOLD


@pytest.mark.parametrize("arg", ["8", "008", "NO.008", "no.008"])
def test_number_formats(path: Path, arg: str) -> None:
    assert hold.main(["clear", arg], path) == 0
    assert holds(path)[8] is None


@pytest.mark.parametrize("argv", [["clear"], ["clear", "abc"], ["reject"], ["bogus", "1"], ["clear", "8", "x"]])
def test_usage_error(path: Path, argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    before = path.read_bytes()
    assert hold.main(argv, path) == 2
    assert "使い方" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_cleared_work_becomes_visible(path: Path) -> None:
    works = load_catalog(path)
    assert next_pending(works).no == 1
    hold.main(["clear", "8"], path)
    works = load_catalog(path)
    assert stock_count(works) == 2
    works[0].suzuri_poster_id = works[0].suzuri_sticker_id = 1
    works[0].ig_media_id = "x"
    assert next_pending(works).no == 8


def test_rejected_work_is_invisible(path: Path) -> None:
    hold.main(["reject", "8"], path)
    works = load_catalog(path)
    assert [w.no for w in works if w.no == 8 and w.approved]  # approved, yet...
    assert next_pending(works).no == 1
    works[0].suzuri_poster_id = works[0].suzuri_sticker_id = 1
    works[0].ig_media_id = "x"
    assert next_pending(works) is None
    assert stock_count(works) == 0


def test_reject_twice_refused(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    hold.main(["reject", "9"], path)
    capsys.readouterr()
    assert hold.main(["reject", "9"], path) == 1
    assert "すでに登録あり" in capsys.readouterr().out
    assert holds(path)[9] == hold.REJECTED_HOLD


def test_duplicate_number_clears_once(path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert hold.main(["clear", "8", "8", "NO.008"], path) == 0
    assert capsys.readouterr().out.count("NO.008") == 1
    assert holds(path)[8] is None


def test_non_ascii_digits_are_usage_error(path: Path) -> None:
    assert hold.main(["clear", "８"], path) == 2

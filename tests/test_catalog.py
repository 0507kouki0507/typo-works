from typo.catalog import (
    Work,
    is_duplicate,
    load_catalog,
    next_no,
    next_pending,
    save_catalog,
    stock_count,
)


def test_roundtrip(tmp_path):
    w = Work(no=1, phrase="MAKE\nLESS\nNOISE", ja="静かに、強く。", layout="stack", accent_line=1, created="2026-11-22")
    save_catalog(tmp_path / "c.json", [w])
    assert load_catalog(tmp_path / "c.json") == [w]


def test_next_pending_skips_unapproved_and_done():
    done = Work(1, "A", "あ", "stack", -1, "2026-11-22", True, 10, 11, [], "ig1", "2026-11-23")
    half = Work(2, "B", "い", "stack", -1, "2026-11-22", True, 20, None, [], None, None)
    unapproved = Work(3, "C", "う", "stack", -1, "2026-11-22")
    assert next_pending([unapproved, half, done]).no == 2
    assert next_pending([done, unapproved]) is None


def test_stock_and_next_no():
    assert stock_count([]) == 0 and next_no([]) == 1


def test_duplicate_ignores_case_and_punctuation():
    w = Work(1, "MAKE\nLESS\nNOISE", "", "stack", -1, "2026-11-22")
    assert is_duplicate("make less noise!", [w])
    assert not is_duplicate("MAKE MORE NOISE", [w])


def test_next_pending_skips_held_work():
    held = Work(1, "A", "あ", "stack", -1, "2026-11-22", True, 10, None, [], None, None, hold="poster: outcome unknown")
    free = Work(2, "B", "い", "stack", -1, "2026-11-22", True)
    assert next_pending([held, free]).no == 2
    assert next_pending([held]) is None


def test_catalog_without_hold_field_still_loads(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(
        '[{"no": 1, "phrase": "A", "ja": "あ", "layout": "stack", "accent_line": -1, "created": "2026-11-22"}]',
        encoding="utf-8",
    )
    [work] = load_catalog(path)
    assert work.hold is None
    assert next_pending([work]) is None  # not approved


def test_stock_count_ignores_held_works():
    waiting = Work(1, "A", "あ", "stack", -1, "2026-11-22", True)
    held = Work(2, "B", "い", "stack", -1, "2026-11-22", True, hold="instagram: post may be published")
    posted = Work(3, "C", "う", "stack", -1, "2026-11-22", True, 30, 31, [], "ig3", "2026-11-23")
    assert stock_count([waiting, held, posted]) == 1


def test_save_failure_keeps_original_and_leaves_no_temp(tmp_path, monkeypatch):
    import os

    path = tmp_path / "catalog.json"
    work = Work(no=1, phrase="A", ja="あ", layout="stack", accent_line=0, created="2026-11-01")
    save_catalog(path, [work])
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    work.ja = "changed"
    try:
        save_catalog(path, [work])
    except OSError:
        pass
    else:
        raise AssertionError("expected OSError")
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["catalog.json"]

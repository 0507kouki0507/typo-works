from dataclasses import replace

import pytest
from PIL import Image, ImageDraw

from typo.catalog import Work
from typo.checks import BANNED_PATH, NOTDEF_PROBE, _glyph_bitmap, check_image, check_phrase, check_work, load_banned
from typo.render import INK, LAYOUTS, MARGIN_RATIO, PAPER, Target, render

W = Work(1, "MAKE\nLESS\nNOISE", "静かに、強く。", "stack", 1, "2026-11-22")


def test_ok():
    assert check_phrase("MAKE\nLESS\nNOISE", "静かに、強く。", [], ["NIKE"]) == []


def test_lowercase_rejected():
    assert check_phrase("make", "あ", [], [])


def test_too_long_line():
    assert check_phrase("ABCDEFGHIJKLM", "あ", [], [])


def test_twelve_chars_ok():
    assert check_phrase("ABCDEFGHIJKL", "あ", [], []) == []


def test_five_lines():
    assert check_phrase("A\nB\nC\nD\nE", "あ", [], [])


def test_blank_line_rejected():
    assert check_phrase("A\n\nB", "あ", [], [])


def test_allowed_symbols():
    assert check_phrase("IT'S 9 & 1,\nOK. NO-WAY!?", "あ", [], []) == []


def test_disallowed_symbol():
    assert check_phrase("HELLO;", "あ", [], [])


def test_banned_substring():
    assert check_phrase("JUST\nDO IT", "あ", [], ["JUST DO IT"])


def test_banned_ignores_empty_entries():
    assert check_phrase("OK", "あ", [], [""]) == []


def test_duplicate():
    assert check_phrase("MAKE LESS\nNOISE", "あ", [Work(1, "MAKE\nLESS\nNOISE", "", "stack", -1, "2026-11-22")], [])


def test_unsupported_glyph():
    assert check_phrase("OK", "𓀀", [], [])


def test_ja_too_long():
    assert check_phrase("OK", "あ" * 17, [], [])


def test_ja_newline_rejected():
    assert check_phrase("OK", "あ\nい", [], [])


def test_empty_ja():
    assert check_phrase("OK", "", [], [])


def test_unknown_layout():
    assert check_work(replace(W, layout="zigzag"), [], [])


def test_work_does_not_duplicate_itself():
    assert check_work(W, [W], []) == []


def test_work_duplicate_of_another():
    assert check_work(replace(W, no=2), [W], [])


def test_banned_file_is_usable():
    banned = load_banned()
    assert BANNED_PATH.exists() and len(banned) >= 30
    assert "JUST DO IT" in banned and "NIKE" in banned
    assert check_phrase("JUST\nDO IT", "あ", [], banned)


def test_blank_image():
    assert check_image(Image.new("RGB", (1080, 1350), PAPER), Target.POST)


def test_wrong_size():
    assert check_image(Image.new("RGB", (100, 100)), Target.POST)


def test_wrong_mode():
    assert check_image(Image.new("L", (1080, 1350), 255), Target.POST)


def test_text_in_margin_rejected():
    img = Image.new("RGB", (1080, 1350), PAPER)
    ImageDraw.Draw(img).rectangle((0, 100, 500, 400), fill=INK)
    assert any("margin" in p for p in check_image(img, Target.POST))


def test_unknown_background_rejected():
    img = Image.new("RGB", (1080, 1350), "#ff00ff")
    ImageDraw.Draw(img).rectangle((300, 300, 600, 600), fill=INK)
    assert check_image(img, Target.POST)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("target", [Target.POST, Target.STICKER])
def test_rendered_work_passes(layout, target):
    assert check_image(render(replace(W, layout=layout), target), target) == []



def _with_block(left: int, top: int, right: int, bottom: int) -> Image.Image:
    img = Image.new("RGB", (1080, 1350), PAPER)
    ImageDraw.Draw(img).rectangle((left, top, right, bottom), fill=INK)
    return img


def test_margin_boundary():
    margin = int(1080 * MARGIN_RATIO)  # 54
    assert check_image(_with_block(margin, margin, 1080 - margin - 1, 1350 - margin - 1), Target.POST) == []
    assert check_image(_with_block(margin - 2, 300, 600, 600), Target.POST) == []  # within the 2px tolerance
    assert check_image(_with_block(margin - 3, 300, 600, 600), Target.POST)  # 3px intrusion
    assert check_image(_with_block(300, 300, 1080 - margin + 2, 600), Target.POST)
    assert check_image(_with_block(300, margin - 20, 600, 600), Target.POST)  # 20px intrusion
    assert check_image(_with_block(300, 300, 600, 1350 - margin + 3), Target.POST)


def test_notdef_probe_draws_something():
    size, pixels = _glyph_bitmap(NOTDEF_PROBE)
    assert any(pixels)
    assert _glyph_bitmap("あ") != (size, pixels)

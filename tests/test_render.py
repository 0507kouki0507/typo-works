from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from tests.conftest import count_pixels_near, ink_bbox_inside_margin
from typo.catalog import Work
from typo.render import LAYOUTS, MARGIN_RATIO, RED, Target, render, save_post_jpeg

W = Work(1, "MAKE\nLESS\nNOISE", "静かに、強く。", "stack", 1, "2026-11-22")


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("target", [Target.POST, Target.STICKER])
def test_size_and_margin(layout, target):
    img = render(replace(W, layout=layout), target)
    assert img.size == target.value and img.mode == "RGB"
    assert ink_bbox_inside_margin(img, MARGIN_RATIO * 0.5)


def test_poster_ratio_at_small_scale():
    img = render(W, Target.POSTER, scale=0.1)
    assert img.size == (1020, 1431)  # 切り捨て


def test_long_line_still_fits():
    img = render(replace(W, phrase="WWWWWWWWWWWW\nI"), Target.POST)
    assert ink_bbox_inside_margin(img, MARGIN_RATIO * 0.5)


def test_accent_line_is_red():
    img = render(W, Target.POST)
    assert count_pixels_near(img, RED) > 1000


def test_deterministic():
    assert render(W, Target.POST).tobytes() == render(W, Target.POST).tobytes()


def test_post_jpeg(tmp_path):
    save_post_jpeg(W, tmp_path / "p.jpg")
    assert Image.open(tmp_path / "p.jpg").format == "JPEG"


@pytest.mark.parametrize("layout", LAYOUTS)
def test_accent_line_is_red_in_every_layout(layout):
    img = render(replace(W, layout=layout), Target.POST)
    assert count_pixels_near(img, RED) > 1000


def _phrase_ink_width(img: Image.Image) -> int:
    """Ink width of the upper 85% of the canvas, i.e. the phrase without the footer rule."""
    phrase_area = img.crop((0, 0, img.width, int(img.height * 0.85)))
    background = Image.new("RGB", phrase_area.size, img.getpixel((0, 0)))
    left, _, right, _ = ImageChops.difference(phrase_area, background).convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    return right - left


@pytest.mark.parametrize("layout", ["stack", "invert"])
def test_stack_fills_content_width(layout):
    img = render(replace(W, layout=layout, ja=""), Target.POST)
    content_width = img.width - 2 * round(min(img.size) * MARGIN_RATIO)
    assert _phrase_ink_width(img) >= 0.85 * content_width


@pytest.mark.parametrize("phrase", ["", "\n", "  \n \n"])
def test_blank_phrase_raises(phrase):
    with pytest.raises(ValueError, match="empty phrase"):
        render(replace(W, phrase=phrase), Target.POST)


def test_fit_rotated_gives_up_instead_of_looping():
    from typo.render import HEADLINE_FONT, Line, _fit_rotated

    with pytest.raises(ValueError):
        _fit_rotated([Line("MAKE", HEADLINE_FONT, 100, RED)], 1, 1)

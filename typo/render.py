"""Typographic renderer: turns a Work into a post, poster or sticker image."""

import io
import math
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from typo.catalog import Work


class Target(Enum):
    POST = (1080, 1350)
    POSTER = (10205, 14315)
    STICKER = (630, 630)


LAYOUTS: tuple[str, ...] = ("stack", "giant", "diagonal", "invert")
MARGIN_RATIO = 0.05  # 短辺に対する余白。文字はこの内側に収める
PAPER, INK, RED = "#f2efe8", "#111111", "#e63322"

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
HEADLINE_FONT = FONTS_DIR / "anton" / "Anton-Regular.ttf"
SUBHEAD_FONT = FONTS_DIR / "archivoblack" / "ArchivoBlack-Regular.ttf"
MONO_FONT = FONTS_DIR / "spacemono" / "SpaceMono-Regular.ttf"
JA_FONT = FONTS_DIR / "notosansjp" / "NotoSansJP[wght].ttf"
STACK_FONTS = (HEADLINE_FONT, SUBHEAD_FONT)  # stack/invert/sticker pick whichever fills more width
JA_WEIGHT = 700  # Noto Sans JP is a variable font; 700 = Bold

REFERENCE_SIZE = 1000  # font size at which ink boxes are measured, then scaled
LEADING = 0.1  # gap between lines, relative to the smaller line's ink height
MAX_LINE_CONTRAST = 3.0  # a line's font size is capped at this × the smallest line's
GIANT_REST_WIDTH = 0.62  # width share for the small lines under the giant one
MAX_FIT_STEPS = 200  # safety cap for the diagonal shrink loop (it normally ends in 1–3 steps)
DIAGONAL_DEGREES = -8  # CSS-style: negative = counter-clockwise, text rises to the right
FOOTER_TEXT_RATIO = 0.024  # footer text ink height, relative to the short side
RULE_RATIO = 0.0018  # rule thickness, relative to the short side


@dataclass(frozen=True)
class Line:
    text: str
    font_path: Path
    size: int
    color: str


@lru_cache(maxsize=256)
def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), size)
    if path == JA_FONT:
        font.set_variation_by_axes([JA_WEIGHT])
    return font


@lru_cache(maxsize=256)
def _reference_ink_box(text: str, path: Path) -> tuple[int, int, int, int]:
    """True ink box (no side bearings) at REFERENCE_SIZE, relative to the left baseline origin."""
    font = _font(path, REFERENCE_SIZE)
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    pad = REFERENCE_SIZE // 10
    canvas = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
    origin = (pad - left, pad - top)
    ImageDraw.Draw(canvas).text(origin, text, font=font, fill=255, anchor="ls")
    ink = canvas.getbbox() or (origin[0], origin[1], origin[0], origin[1])
    return ink[0] - origin[0], ink[1] - origin[1], ink[2] - origin[0], ink[3] - origin[1]


def _ink_box(text: str, path: Path, size: int) -> tuple[int, int, int, int]:
    """Ink bounding box at `size`, relative to the left baseline origin (outlines scale linearly)."""
    k = size / REFERENCE_SIZE
    left, top, right, bottom = _reference_ink_box(text, path)
    return math.floor(left * k), math.floor(top * k), math.ceil(right * k), math.ceil(bottom * k)


def _ink_size(text: str, path: Path, size: int) -> tuple[int, int]:
    left, top, right, bottom = _ink_box(text, path, size)
    return right - left, bottom - top


def _fit_size(text: str, path: Path, width: int) -> int:
    """Largest font size whose ink width is at most `width` (binary search)."""
    low, high = 1, max(2, width * 4)
    while low < high:
        mid = (low + high + 1) // 2
        if _ink_size(text, path, mid)[0] <= width:
            low = mid
        else:
            high = mid - 1
    return low


def _scaled(lines: list[Line], factor: float) -> list[Line]:
    return [Line(ln.text, ln.font_path, max(1, int(ln.size * factor)), ln.color) for ln in lines]


def _block_size(lines: list[Line]) -> tuple[int, int]:
    sizes = [_ink_size(ln.text, ln.font_path, ln.size) for ln in lines]
    gaps = sum(_gap(a[1], b[1]) for a, b in zip(sizes, sizes[1:]))
    return max(w for w, _ in sizes), sum(h for _, h in sizes) + gaps


def _gap(height_a: int, height_b: int) -> int:
    return round(min(height_a, height_b) * LEADING)


def _shrink_to_fit(lines: list[Line], width: int, height: int) -> list[Line]:
    """Uniformly shrink the lines until the block fits inside width × height."""
    if _fits(lines, width, height):
        return lines
    low, high = 0.0, 1.0
    for _ in range(24):
        mid = (low + high) / 2
        if _fits(_scaled(lines, mid), width, height):
            low = mid
        else:
            high = mid
    return _scaled(lines, low)


def _fits(lines: list[Line], width: int, height: int) -> bool:
    block_w, block_h = _block_size(lines)
    return block_w <= width and block_h <= height


def _draw_block(draw: ImageDraw.ImageDraw, lines: list[Line], x: int, y: int, fill: dict[str, int | str] | None = None) -> None:
    """Draw lines top-down with their ink left edges at x and the first ink top at y."""
    previous_height: int | None = None
    for ln in lines:
        left, top, _, bottom = _ink_box(ln.text, ln.font_path, ln.size)
        if previous_height is not None:
            y += _gap(previous_height, bottom - top)
        color = fill[ln.color] if fill else ln.color
        draw.text((x - left, y - top), ln.text, font=_font(ln.font_path, ln.size), fill=color, anchor="ls")
        y += bottom - top
        previous_height = bottom - top


def _phrase_lines(work: Work) -> list[str]:
    lines = [t.strip() for t in work.phrase.upper().split("\n") if t.strip()]
    if not lines:
        raise ValueError("empty phrase")
    return lines


def _color(work: Work, index: int, base: str) -> str:
    return RED if index == work.accent_line else base


def _stack_lines(work: Work, width: int, base: str, font: Path = HEADLINE_FONT) -> list[Line]:
    texts = _phrase_lines(work)
    sizes = [_fit_size(t, font, width) for t in texts]
    cap = int(min(sizes) * MAX_LINE_CONTRAST)
    return [Line(t, font, min(size, cap), _color(work, i, base)) for i, (t, size) in enumerate(zip(texts, sizes))]


def _widest_stack(work: Work, width: int, height: int, base: str) -> list[Line]:
    """Fit the stack in each heading font and keep the one that fills more width (Anton wins ties)."""
    candidates = [_shrink_to_fit(_stack_lines(work, width, base, font), width, height) for font in STACK_FONTS]
    return max(candidates, key=lambda lines: _block_size(lines)[0])


def _giant_lines(work: Work, width: int, base: str) -> list[Line]:
    first, *rest = _phrase_lines(work)
    lines = [Line(first, HEADLINE_FONT, _fit_size(first, HEADLINE_FONT, width), _color(work, 0, base))]
    if rest:
        rest_width = int(width * GIANT_REST_WIDTH)
        size = min(_fit_size(t, SUBHEAD_FONT, rest_width) for t in rest)
        lines += [Line(t, SUBHEAD_FONT, size, _color(work, i, base)) for i, t in enumerate(rest, start=1)]
    return lines


def _rotated_box(width: int, height: int) -> tuple[int, int]:
    rad = math.radians(abs(DIAGONAL_DEGREES))
    return (
        math.ceil(width * math.cos(rad) + height * math.sin(rad)),
        math.ceil(width * math.sin(rad) + height * math.cos(rad)),
    )


def _fit_rotated(lines: list[Line], width: int, height: int) -> list[Line]:
    """Shrink lines until their block, once rotated, fits inside width × height."""
    factor = 1.0
    for _ in range(MAX_FIT_STEPS):
        candidate = _scaled(lines, factor)
        rot_w, rot_h = _rotated_box(*_block_size(candidate))
        if rot_w <= width and rot_h <= height:
            return candidate
        factor *= min(width / rot_w, height / rot_h, 0.995)
    raise ValueError("phrase cannot be fitted diagonally")


def _paste_rotated(img: Image.Image, lines: list[Line], area: tuple[int, int, int, int]) -> None:
    """Render the block onto masks, rotate them, and paste centred inside area."""
    block_w, block_h = _block_size(lines)
    colors = sorted({ln.color for ln in lines})
    for color in colors:
        mask = Image.new("L", (block_w, block_h), 0)
        fill = {c: (255 if c == color else 0) for c in colors}
        _draw_block(ImageDraw.Draw(mask), lines, 0, 0, fill)
        rotated = mask.rotate(-DIAGONAL_DEGREES, resample=Image.Resampling.BICUBIC, expand=True)
        x0, y0, x1, y1 = area
        x = x0 + (x1 - x0 - rotated.width) // 2
        y = y0 + (y1 - y0 - rotated.height) // 2
        img.paste(color, (x, y), rotated)


def _footer(draw: ImageDraw.ImageDraw, work: Work, size: tuple[int, int], margin: int, ink: str) -> int:
    """Draw the rule, number and Japanese line; return the y where the text area must end."""
    width, height = size
    short = min(size)
    text_h = max(1, round(short * FOOTER_TEXT_RATIO))
    number = f"NO.{work.no:03d}"
    baseline = height - margin
    mono_size = _size_for_cap_height(MONO_FONT, text_h)
    no_box = _ink_box(number, MONO_FONT, mono_size)
    draw.text((margin - no_box[0], baseline - no_box[3]), number, font=_font(MONO_FONT, mono_size), fill=ink, anchor="ls")
    if work.ja:
        ja_size = _size_for_cap_height(JA_FONT, text_h)
        ja_box = _ink_box(work.ja, JA_FONT, ja_size)
        draw.text((width - margin - ja_box[2], baseline - ja_box[3]), work.ja, font=_font(JA_FONT, ja_size), fill=ink, anchor="ls")
    rule_h = max(1, round(short * RULE_RATIO))
    rule_bottom = baseline - text_h - round(text_h * 0.9)
    draw.rectangle((margin, rule_bottom - rule_h, width - margin - 1, rule_bottom - 1), fill=ink)
    return rule_bottom - rule_h - round(short * 0.04)


def _size_for_cap_height(path: Path, height: int) -> int:
    """Font size whose cap-height ('H') ink is about `height` pixels."""
    cap = _ink_size("H", path, REFERENCE_SIZE)[1]
    return max(1, round(REFERENCE_SIZE * height / cap))


def _canvas_size(target: Target, scale: float) -> tuple[int, int]:
    width, height = target.value
    return int(width * scale), int(height * scale)


def render(work: Work, target: Target, scale: float = 1.0) -> Image.Image:
    """Render the work for a target. `scale` shrinks the canvas (for tests and previews)."""
    size = _canvas_size(target, scale)
    layout = "stack" if target is Target.STICKER else work.layout
    ground, ink = (INK, PAPER) if layout == "invert" else (PAPER, INK)
    img = Image.new("RGB", size, ground)
    draw = ImageDraw.Draw(img)
    margin = round(min(size) * MARGIN_RATIO)
    bottom = size[1] - margin
    if target is not Target.STICKER:
        bottom = _footer(draw, work, size, margin, ink)
    area = (margin, margin, size[0] - margin, bottom)
    _draw_phrase(img, draw, work, layout, area, ink, center=target is Target.STICKER)
    return img


def _draw_phrase(img: Image.Image, draw: ImageDraw.ImageDraw, work: Work, layout: str, area: tuple[int, int, int, int], ink: str, center: bool) -> None:
    x0, y0, x1, y1 = area
    width, height = x1 - x0, y1 - y0
    if layout == "diagonal":
        _paste_rotated(img, _fit_rotated(_stack_lines(work, width, ink), width, height), area)
        return
    if layout == "giant":
        lines = _shrink_to_fit(_giant_lines(work, width, ink), width, height)
    else:
        lines = _widest_stack(work, width, height, ink)
    if layout == "giant" and len(lines) > 1:
        _draw_block(draw, lines[:1], x0, y0)
        _draw_block(draw, lines[1:], x0, y1 - _block_size(lines[1:])[1])
        return
    block_w, block_h = _block_size(lines)
    y = y0 + (height - block_h) // 2 if center else y0
    x = x0 + (width - block_w) // 2 if center else x0
    _draw_block(draw, lines, x, y)


def save_post_jpeg(work: Work, path: Path) -> None:
    render(work, Target.POST).save(path, "JPEG", quality=92)


def png_bytes(work: Work, target: Target) -> bytes:
    buffer = io.BytesIO()
    render(work, target).save(buffer, "PNG", optimize=True)
    return buffer.getvalue()

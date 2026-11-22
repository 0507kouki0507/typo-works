"""Machine inspection: decides whether a work may enter the stock."""

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFont

from typo.catalog import Work, is_duplicate, normalize_phrase
from typo.render import JA_FONT, JA_WEIGHT, LAYOUTS, MARGIN_RATIO, INK, PAPER, Target

BANNED_PATH = Path(__file__).resolve().parent / "banned.txt"

MAX_LINES = 4
MAX_LINE_CHARS = 12
MAX_JA_CHARS = 16
PHRASE_CHARS = re.compile(r"[A-Z0-9 .,!?'&-]+")
MARGIN_TOLERANCE_PX = 2  # absolute: rounding and anti-aliasing overshoot is pixel-level, not proportional to size
BACKGROUND_TOLERANCE = 8  # JPEG noise on the ground colour
INK_TOLERANCE = 24  # a pixel this far from the ground counts as ink
NOTDEF_PROBE = "\uffff"  # never mapped in a font, so it draws the font's missing-glyph box


def load_banned(path: Path = BANNED_PATH) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def check_phrase(phrase: str, ja: str, works: list[Work], banned: list[str]) -> list[str]:
    """Return a description of every problem; an empty list means the words pass."""
    return [*_phrase_problems(phrase), *_ja_problems(ja), *_banned_problems(phrase, banned), *_duplicate_problems(phrase, works)]


def _phrase_problems(phrase: str) -> list[str]:
    lines = phrase.split("\n")
    problems = [] if 1 <= len(lines) <= MAX_LINES else [f"phrase has {len(lines)} lines (1-{MAX_LINES})"]
    for number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            problems.append(f"line {number} is blank")
        elif len(text) > MAX_LINE_CHARS:
            problems.append(f"line {number} has {len(text)} characters (max {MAX_LINE_CHARS})")
        elif not PHRASE_CHARS.fullmatch(text):
            problems.append(f"line {number} has characters outside A-Z 0-9 space . , ! ? ' & -")
    return problems


def _ja_problems(ja: str) -> list[str]:
    if not 1 <= len(ja) <= MAX_JA_CHARS:
        return [f"ja has {len(ja)} characters (1-{MAX_JA_CHARS})"]
    missing = sorted({ch for ch in ja if not _has_glyph(ch)})
    return [f"ja has characters missing from the font: {''.join(missing)}"] if missing else []


@lru_cache(maxsize=None)
def _glyph_bitmap(char: str) -> tuple[tuple[int, int], bytes]:
    font = ImageFont.truetype(str(JA_FONT), 64)
    font.set_variation_by_axes([JA_WEIGHT])
    mask = font.getmask(char)
    return mask.size, bytes(mask)


def _has_glyph(char: str) -> bool:
    """A character is covered unless it draws exactly the font's missing-glyph box."""
    if not char.isprintable():
        return False
    return char.isspace() or _glyph_bitmap(char) != _glyph_bitmap(NOTDEF_PROBE)


def _banned_problems(phrase: str, banned: list[str]) -> list[str]:
    key = normalize_phrase(phrase)
    hits = [word for word in banned if (norm := normalize_phrase(word)) and norm in key]
    return [f"contains banned word: {word}" for word in hits]


def _duplicate_problems(phrase: str, works: list[Work]) -> list[str]:
    return ["phrase duplicates an existing work"] if is_duplicate(phrase, works) else []


def check_image(img: Image.Image, target: Target) -> list[str]:
    """Return a description of every problem; an empty list means the image passes."""
    if img.size != target.value:
        return [f"size is {img.size}, expected {target.value}"]
    if img.mode != "RGB":
        return [f"mode is {img.mode}, expected RGB"]
    ground = img.getpixel((0, 0))
    if not any(_close(ground, colour) for colour in (PAPER, INK)):
        return [f"background {ground} is neither paper nor ink"]
    if not _has_ink(img, ground):
        return ["image is blank"]
    return _margin_problems(img, ground)


def _close(pixel: tuple[int, int, int], colour: str) -> bool:
    expected = Image.new("RGB", (1, 1), colour).getpixel((0, 0))
    return all(abs(a - b) <= BACKGROUND_TOLERANCE for a, b in zip(pixel, expected))


def _has_ink(img: Image.Image, ground: tuple[int, int, int]) -> bool:
    """True if any pixel differs from the ground by more than INK_TOLERANCE in some channel."""
    return any(
        high - g > INK_TOLERANCE or g - low > INK_TOLERANCE
        for (low, high), g in zip(img.getextrema(), ground)
    )


def _margin_problems(img: Image.Image, ground: tuple[int, int, int]) -> list[str]:
    """Look only at the four margin strips, so the cost stays small even for the poster."""
    width, height = img.size
    margin = int(min(img.size) * MARGIN_RATIO) - MARGIN_TOLERANCE_PX
    strips = {
        "top": (0, 0, width, margin),
        "bottom": (0, height - margin, width, height),
        "left": (0, 0, margin, height),
        "right": (width - margin, 0, width, height),
    }
    sides = [side for side, box in strips.items() if _has_ink(img.crop(box), ground)]
    return [f"text reaches into the {side} margin" for side in sides]


def check_work(work: Work, works: list[Work], banned: list[str]) -> list[str]:
    """Phrase checks (ignoring the work's own catalog entry) plus the layout name."""
    others = [w for w in works if w.no != work.no]
    problems = check_phrase(work.phrase, work.ja, others, banned)
    if work.layout not in LAYOUTS:
        problems.append(f"unknown layout: {work.layout}")
    return problems

"""Work catalog: the list of typographic works and their publishing state."""

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Work:
    no: int
    phrase: str  # newline-separated, e.g. "MAKE\nLESS\nNOISE"
    ja: str  # accompanying Japanese text
    layout: str  # "stack" | "giant" | "diagonal" | "invert"
    accent_line: int  # 0-based red line, -1 for none
    created: str  # "YYYY-MM-DD"
    approved: bool = False
    suzuri_poster_id: int | None = None
    suzuri_sticker_id: int | None = None
    product_urls: list[str] = field(default_factory=list)
    ig_media_id: str | None = None
    posted_at: str | None = None
    hold: str | None = None  # set when a public side effect's outcome is unknown; a person must resolve it


def load_catalog(path: Path) -> list[Work]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Work(**item) for item in data]


def save_catalog(path: Path, works: list[Work]) -> None:
    """Write via a temp file in the same directory, then swap it in: a crash never leaves a half-written catalog."""
    ordered = sorted(works, key=lambda w: w.no)
    text = json.dumps([asdict(w) for w in ordered], ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def next_pending(works: list[Work]) -> Work | None:
    pending = [
        w
        for w in works
        if w.approved
        and w.hold is None
        and (w.suzuri_poster_id is None or w.suzuri_sticker_id is None or w.ig_media_id is None)
    ]
    return min(pending, key=lambda w: w.no, default=None)


def stock_count(works: list[Work]) -> int:
    return sum(1 for w in works if w.approved and w.hold is None and w.ig_media_id is None)


def next_no(works: list[Work]) -> int:
    return max((w.no for w in works), default=0) + 1


def normalize_phrase(phrase: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", phrase.upper())


def is_duplicate(phrase: str, works: list[Work]) -> bool:
    key = normalize_phrase(phrase)
    return any(normalize_phrase(w.phrase) == key for w in works)

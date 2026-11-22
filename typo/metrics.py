"""Weekly numbers: followers, media count, and the last 7 days' reach and saves, one CSV row per run."""

import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path

from typo.catalog import Work
from typo.instagram import Instagram, InstagramError

HEADER = ["date", "followers", "media_count", "reach_7d", "saved_7d"]
WINDOW_DAYS = 7

log = logging.getLogger(__name__)


def _recent(works: list[Work], today: str) -> list[Work]:
    """Works posted in the 7 days up to and including today."""
    end = date.fromisoformat(today)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    return [
        w for w in works
        if w.ig_media_id and w.posted_at and start <= date.fromisoformat(w.posted_at) <= end
    ]


def _totals(ig: Instagram, works: list[Work]) -> tuple[int, int]:
    reach = saved = 0
    for work in works:
        try:
            insights = ig.media_insights(work.ig_media_id or "")
        except InstagramError as error:  # e.g. a post deleted by hand
            log.warning("insights unavailable for NO.%03d: %s", work.no, error)
            continue
        reach += insights["reach"]
        saved += insights["saved"]
    return reach, saved


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def record_weekly(ig: Instagram, works: list[Work], csv_path: Path, today: str) -> dict:
    """Add (or replace, for the same date) this week's row and return it.

    If the account numbers cannot be read, nothing is written and the error is raised.
    """
    stats = ig.account_stats()
    reach, saved = _totals(ig, _recent(works, today))
    row = {
        "date": today,
        "followers": stats["followers_count"],
        "media_count": stats["media_count"],
        "reach_7d": reach,
        "saved_7d": saved,
    }
    rows = [r for r in _read_rows(csv_path) if r["date"] != today]
    _write_rows(csv_path, [*rows, row])
    return row

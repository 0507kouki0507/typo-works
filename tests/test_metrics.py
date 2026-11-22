"""Weekly numbers. Instagram is a fake; nothing touches the network."""

import csv
from pathlib import Path

import pytest

from typo.catalog import Work
from typo.instagram import InstagramError
from typo.metrics import HEADER, record_weekly

TODAY = "2026-11-23"


class FakeInstagram:
    def __init__(self, insights: dict[str, dict], followers: int = 10, media: int = 5, stats_fail: bool = False) -> None:
        self.insights = insights
        self.followers = followers
        self.media = media
        self.stats_fail = stats_fail

    def account_stats(self) -> dict:
        if self.stats_fail:
            raise InstagramError("stats failed")
        return {"followers_count": self.followers, "media_count": self.media}

    def media_insights(self, media_id: str) -> dict:
        if media_id not in self.insights:
            raise InstagramError("gone")
        return self.insights[media_id]


def work(no: int, posted_at: str | None, media_id: str | None = None) -> Work:
    return Work(no=no, phrase="A", ja="あ", layout="stack", accent_line=-1, created="2026-11-01", ig_media_id=media_id, posted_at=posted_at)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_appends_row(tmp_path: Path) -> None:
    path = tmp_path / "metrics" / "weekly.csv"
    ig = FakeInstagram({"a": {"reach": 100, "saved": 3}, "b": {"reach": 50, "saved": 2}, "old": {"reach": 999, "saved": 99}})
    works = [work(1, "2026-11-17", "a"), work(2, "2026-11-23", "b"), work(3, "2026-11-16", "old"), work(4, None)]
    row = record_weekly(ig, works, path, TODAY)
    assert row == {"date": TODAY, "followers": 10, "media_count": 5, "reach_7d": 150, "saved_7d": 5}
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(HEADER) == "date,followers,media_count,reach_7d,saved_7d"
    assert rows(path) == [{k: str(v) for k, v in row.items()}]
    record_weekly(ig, works, path, "2026-11-30")
    assert [r["date"] for r in rows(path)] == [TODAY, "2026-11-30"]


def test_same_day_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "weekly.csv"
    record_weekly(FakeInstagram({}, followers=10), [], path, TODAY)
    record_weekly(FakeInstagram({}, followers=12), [], path, TODAY)
    assert [(r["date"], r["followers"]) for r in rows(path)] == [(TODAY, "12")]


def test_no_posts_gives_zero(tmp_path: Path) -> None:
    row = record_weekly(FakeInstagram({}), [work(1, "2026-10-01", "a")], tmp_path / "weekly.csv", TODAY)
    assert row["reach_7d"] == 0 and row["saved_7d"] == 0


def test_failed_insights_skip_that_work(tmp_path: Path) -> None:
    ig = FakeInstagram({"b": {"reach": 7, "saved": 1}})
    row = record_weekly(ig, [work(1, TODAY, "deleted"), work(2, TODAY, "b")], tmp_path / "weekly.csv", TODAY)
    assert (row["reach_7d"], row["saved_7d"]) == (7, 1)


def test_stats_failure_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "weekly.csv"
    with pytest.raises(InstagramError):
        record_weekly(FakeInstagram({}, stats_fail=True), [], path, TODAY)
    assert not path.exists()

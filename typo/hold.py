"""Weekly trademark check tool: list held works, release them, or mark them as found registered.

    python -m typo.hold                 list held works
    python -m typo.hold clear 8 9 10    release works whose phrase is clear on J-PlatPat
    python -m typo.hold reject 9        mark a work whose phrase was found registered (never posted)

Only works held for the trademark check can be cleared or rejected. Any other hold
(a failed SUZURI or Instagram step) needs a person to look at it, so it is left alone.
"""

import re
import sys
from collections.abc import Sequence
from pathlib import Path

from typo.catalog import Work, load_catalog, save_catalog
from typo.daily import CATALOG_PATH
from typo.weekly import TRADEMARK_HOLD

REJECTED_HOLD = "trademark found"  # permanent: the work is never posted and is not offered for clearing

USAGE = "使い方: python -m typo.hold [list] | clear 番号... | reject 番号...  （番号は 8 / 008 / NO.008 のどれでもOK）"
NUMBER_RE = re.compile(r"(?:NO\.)?([0-9]+)", re.IGNORECASE)


def parse_numbers(args: Sequence[str]) -> list[int] | None:
    """Return the work numbers, or None if there are none or any is not a number."""
    numbers: list[int] = []
    for arg in args:
        match = NUMBER_RE.fullmatch(arg.strip())
        if match is None:
            return None
        numbers.append(int(match.group(1)))
    return list(dict.fromkeys(numbers)) or None


def label(no: int) -> str:
    return f"NO.{no:03d}"


def describe(work: Work) -> str:
    phrase = " / ".join(work.phrase.split("\n"))
    return f"{label(work.no)}  {phrase}  {work.ja}  {work.hold}"


def show_list(works: list[Work]) -> int:
    held = sorted((w for w in works if w.hold is not None), key=lambda w: w.no)
    if not held:
        print("保留中の作品はありません。")
        return 0
    checking = [w for w in held if w.hold == TRADEMARK_HOLD]
    others = [w for w in held if w.hold != TRADEMARK_HOLD]
    print(f"商標チェック待ち（{len(checking)}件）")
    for work in checking:
        print(describe(work))
    if others:
        print(f"その他の保留（{len(others)}件）")
        for work in others:
            print(describe(work))
    print(f"保留は合計{len(held)}件です。")
    return 0


def change_holds(works: list[Work], numbers: list[int], new_hold: str | None, done_word: str) -> tuple[list[str], list[str]]:
    """Apply new_hold to trademark-pending works. Return (done lines, skipped lines)."""
    by_no = {w.no: w for w in works}
    done: list[str] = []
    skipped: list[str] = []
    for no in numbers:
        work = by_no.get(no)
        if work is None:
            skipped.append(f"{label(no)}: そんな番号の作品はありません")
        elif work.hold is None:
            skipped.append(f"{label(no)}: 保留されていません（何もしていません）")
        elif work.hold == REJECTED_HOLD:
            skipped.append(f"{label(no)}: すでに登録ありとして外してあります（もう投稿されません）")
        elif work.hold != TRADEMARK_HOLD:
            skipped.append(f"{label(no)}: 商標チェック以外の保留（{work.hold}）なので触りません。Meariに相談してください")
        else:
            work.hold = new_hold
            done.append(f"{label(no)}: {done_word}")
    return done, skipped


def run_change(catalog_path: Path, numbers: list[int], new_hold: str | None, done_word: str) -> int:
    works = load_catalog(catalog_path)
    done, skipped = change_holds(works, numbers, new_hold, done_word)
    if done:
        save_catalog(catalog_path, works)
    for line in done + skipped:
        print(line)
    return 0 if not skipped else 1


def main(argv: Sequence[str] | None = None, catalog_path: Path = CATALOG_PATH) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "list"
    if command == "list" and len(args) <= 1:
        return show_list(load_catalog(catalog_path))
    numbers = parse_numbers(args[1:])
    if numbers is not None and command == "clear":
        return run_change(catalog_path, numbers, None, "保留を解除しました")
    if numbers is not None and command == "reject":
        return run_change(catalog_path, numbers, REJECTED_HOLD, "登録済みとして投稿対象から外しました")
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())

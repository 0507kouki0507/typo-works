"""Stock check: exits 1 when fewer than MIN_STOCK approved works are waiting to be posted."""

import sys
from pathlib import Path

from typo.catalog import load_catalog, stock_count
from typo.daily import CATALOG_PATH

MIN_STOCK = 3


def main(catalog_path: Path = CATALOG_PATH) -> int:
    count = stock_count(load_catalog(catalog_path))
    if count < MIN_STOCK:
        print(f"stock low: {count}")
        return 1
    print(f"stock ok: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

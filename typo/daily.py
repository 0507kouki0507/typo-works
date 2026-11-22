"""Daily run: list the next approved work on SUZURI, post it to Instagram, write the state back.

Listings and posts are public and not idempotent. When the outcome of one is
unknown, the work gets a `hold` reason and is skipped until a person clears it.
"""

import io
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from typo.caption import build_caption
from typo.catalog import Work, load_catalog, next_pending, save_catalog
from typo.checks import check_image
from typo.instagram import Instagram, InstagramError
from typo.render import Target, render
from typo.suzuri import Suzuri, SuzuriError

RAW_BASE = "https://raw.githubusercontent.com/0507kouki0507/typo-works/main"
POSTER_PRICE, STICKER_PRICE = 800, 200
POSTER_ITEM, STICKER_ITEM = "tarpaulin", "sticker"  # GET /items の name。Task 12の実確認で違えば直す
MAX_PNG_BYTES = 15_000_000  # SUZURI's upload limit (15MB); decimal MB, the stricter reading

CATALOG_PATH = Path(__file__).resolve().parent.parent / "works" / "catalog.json"
ENV_NAMES = ("IG_ACCESS_TOKEN", "IG_USER_ID", "SUZURI_API_KEY")
JST = timezone(timedelta(hours=9))

Renderer = Callable[[Work, Target], Image.Image]
Checker = Callable[[Image.Image, Target], list[str]]
Save = Callable[[], None]


class ImageCheckError(RuntimeError):
    """A rendered image failed check_image; nothing was uploaded."""


class ConfigError(RuntimeError):
    """A required environment variable is missing. The message names it, never a value."""


@dataclass(frozen=True)
class Product:
    step: str
    target: Target
    item: str
    price: int
    id_field: str


PRODUCTS = (
    Product("poster", Target.POSTER, POSTER_ITEM, POSTER_PRICE, "suzuri_poster_id"),
    Product("sticker", Target.STICKER, STICKER_ITEM, STICKER_PRICE, "suzuri_sticker_id"),
)


def run_daily(
    catalog_path: Path,
    ig: Instagram,
    suzuri: Suzuri,
    today: str,
    *,
    renderer: Renderer = render,
    checker: Checker = check_image,
) -> str:
    """Publish one work. Every step is saved right after it; errors propagate after saving."""
    works = load_catalog(catalog_path)
    work = next_pending(works)
    if work is None:
        return "nothing to post"

    def save() -> None:
        save_catalog(catalog_path, works)

    for product in PRODUCTS:
        if getattr(work, product.id_field) is None:
            _list_product(work, product, suzuri, renderer, checker, save)
    if work.ig_media_id is None:
        _post(work, ig, today, save)
    return f"posted NO.{work.no:03d}"


def _list_product(
    work: Work, product: Product, suzuri: Suzuri, renderer: Renderer, checker: Checker, save: Save
) -> None:
    png = _checked_png(work, product, renderer, checker, save)
    item_id = suzuri.item_id(product.item)
    try:
        material_id, urls = suzuri.create_material(_title(work), png, product.price, item_id)
    except Exception as error:
        _record_listing_failure(work, product, error)
        save()
        raise
    setattr(work, product.id_field, material_id)
    work.product_urls.extend(urls)
    save()


def _checked_png(work: Work, product: Product, renderer: Renderer, checker: Checker, save: Save) -> bytes:
    img = renderer(work, product.target)
    problems = checker(img, product.target)
    if problems:
        work.hold = f"{product.step}: image check failed: {problems[0]}"
        save()
        raise ImageCheckError(work.hold)
    png = _png(img)
    if len(png) > MAX_PNG_BYTES:
        work.hold = f"{product.step}: PNG is {len(png)} bytes, over the {MAX_PNG_BYTES}-byte upload limit"
        save()
        raise ImageCheckError(work.hold)
    return png


def _png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _title(work: Work) -> str:
    return f"{' '.join(work.phrase.split())} NO.{work.no:03d}"


def _record_listing_failure(work: Work, product: Product, error: Exception) -> None:
    """Hold the work unless SUZURI says nothing was created. Anything unexpected counts as unknown."""
    if isinstance(error, SuzuriError) and not error.maybe_created:
        return
    material_id = error.material_id if isinstance(error, SuzuriError) else None
    known = ""
    if material_id is not None:
        setattr(work, product.id_field, material_id)
        known = f" (material {material_id})"
    work.hold = f"{product.step}: {type(error).__name__}, listing may exist on SUZURI{known}"


def _post(work: Work, ig: Instagram, today: str, save: Save) -> None:
    image_url = f"{RAW_BASE}/works/{work.no:03d}/post.jpg"
    caption = build_caption(work)
    try:
        media_id = ig.publish_image(image_url, caption)
    except Exception as error:
        if not isinstance(error, InstagramError) or error.maybe_published:
            work.hold = f"instagram: {type(error).__name__}, post may be published"
        save()
        raise
    work.ig_media_id = media_id
    work.posted_at = today
    save()


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ConfigError(f"missing environment variable: {name}")
    return value


def _today() -> str:
    return datetime.now(JST).date().isoformat()


def main(catalog_path: Path = CATALOG_PATH) -> int:
    try:
        token, user_id, api_key = [_require_env(name) for name in ENV_NAMES]
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 1
    ig = Instagram(user_id, token)
    suzuri = Suzuri(api_key)
    print(run_daily(catalog_path, ig, suzuri, _today()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

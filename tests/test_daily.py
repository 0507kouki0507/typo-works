"""Daily runner and stock check. SUZURI and Instagram are fakes; no full-size poster is rendered."""

import pytest
from PIL import Image

from typo import daily, stock
from typo.catalog import Work, load_catalog, save_catalog
from typo.daily import (
    POSTER_ITEM,
    POSTER_PRICE,
    RAW_BASE,
    STICKER_ITEM,
    STICKER_PRICE,
    ImageCheckError,
    run_daily,
)
from typo.instagram import InstagramError
from typo.render import Target
from typo.suzuri import SuzuriError

TODAY = "2026-11-25"
ITEM_IDS = {POSTER_ITEM: 7, STICKER_ITEM: 8}


class FakeSuzuri:
    """Creates materials 101, 102, ...; `errors` maps a call number (1-based) to the error it raises."""

    def __init__(self, errors: dict[int, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.created: list[dict] = []

    def item_id(self, name: str) -> int:
        return ITEM_IDS[name]

    def create_material(self, title: str, png: bytes, price: int, item_id: int, published: bool = True) -> tuple[int, list[str]]:
        self.created.append({"title": title, "png": png, "price": price, "item_id": item_id})
        call = len(self.created)
        if call in self.errors:
            raise self.errors[call]
        material_id = 100 + call
        return material_id, [f"https://suzuri.jp/sample/{material_id}"]


class FakeInstagram:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def publish_image(self, image_url: str, caption: str) -> str:
        self.calls.append((image_url, caption))
        if self.error is not None:
            raise self.error
        return "ig-900"


def tiny_render(work: Work, target: Target) -> Image.Image:
    return Image.new("RGB", (8, 8), "#f2efe8")


def no_problems(img: Image.Image, target: Target) -> list[str]:
    return []


def approved(no: int, **fields: object) -> Work:
    return Work(no, "MAKE\nLESS\nNOISE", "静かに、強く。", "stack", 1, "2026-11-22", True, **fields)


def write(tmp_path, works: list[Work]):
    path = tmp_path / "catalog.json"
    save_catalog(path, works)
    return path


def run(path, ig, suzuri, **kwargs: object) -> str:
    options = {"renderer": tiny_render, "checker": no_problems, **kwargs}
    return run_daily(path, ig, suzuri, TODAY, **options)


def test_happy_path_fills_all_ids(tmp_path):
    path = write(tmp_path, [approved(1)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "posted NO.001"
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id, work.ig_media_id) == (101, 102, "ig-900")
    assert work.posted_at == TODAY
    assert work.product_urls == ["https://suzuri.jp/sample/101", "https://suzuri.jp/sample/102"]
    assert work.hold is None
    poster, sticker = suzuri.created
    assert (poster["price"], poster["item_id"]) == (POSTER_PRICE, ITEM_IDS[POSTER_ITEM])
    assert (sticker["price"], sticker["item_id"]) == (STICKER_PRICE, ITEM_IDS[STICKER_ITEM])
    assert poster["png"].startswith(b"\x89PNG")
    assert "NO.001" in poster["title"]
    image_url, caption = ig.calls[0]
    assert image_url == f"{RAW_BASE}/works/001/post.jpg"
    assert "NO.001" in caption


def test_nothing_to_post_when_empty(tmp_path):
    path = write(tmp_path, [])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "nothing to post"
    assert ig.calls == [] and suzuri.created == []


def test_unapproved_is_not_posted(tmp_path):
    path = write(tmp_path, [Work(1, "A", "あ", "stack", -1, "2026-11-22")])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "nothing to post"
    assert ig.calls == [] and suzuri.created == []


def test_ig_failure_keeps_suzuri_ids(tmp_path):
    path = write(tmp_path, [approved(1)])
    with pytest.raises(InstagramError):
        run(path, FakeInstagram(InstagramError("container failed")), FakeSuzuri())
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id) == (101, 102)
    assert work.ig_media_id is None and work.posted_at is None
    assert work.hold is None


def test_rerun_does_not_recreate_products(tmp_path):
    path = write(tmp_path, [approved(1)])
    with pytest.raises(InstagramError):
        run(path, FakeInstagram(InstagramError("container failed")), FakeSuzuri())
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "posted NO.001"
    assert suzuri.created == []
    assert len(ig.calls) == 1
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id, work.ig_media_id) == (101, 102, "ig-900")


def test_suzuri_failure_skips_instagram(tmp_path):
    path = write(tmp_path, [approved(1)])
    ig = FakeInstagram()
    with pytest.raises(SuzuriError):
        run(path, ig, FakeSuzuri({2: SuzuriError("SUZURI POST /materials failed: HTTP 422")}))
    assert ig.calls == []
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id) == (101, None)


def test_one_work_per_run(tmp_path):
    path = write(tmp_path, [approved(1), approved(2)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "posted NO.001"
    assert len(ig.calls) == 1 and len(suzuri.created) == 2
    first, second = load_catalog(path)
    assert first.ig_media_id == "ig-900" and second.suzuri_poster_id is None


def test_stock_exit_code(tmp_path, capsys):
    path = write(tmp_path, [approved(1), approved(2)])
    assert stock.main(path) == 1
    assert "stock low: 2" in capsys.readouterr().out
    path = write(tmp_path, [approved(1), approved(2), approved(3)])
    assert stock.main(path) == 0


# --- Unknown outcomes: hold the work instead of risking a duplicate public listing or post ---


def test_suzuri_maybe_created_with_id_saves_id_and_holds(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = SuzuriError("SUZURI flagged material 555 as a violation", material_id=555, maybe_created=True)
    ig = FakeInstagram()
    with pytest.raises(SuzuriError):
        run(path, ig, FakeSuzuri({1: error}))
    [work] = load_catalog(path)
    assert work.suzuri_poster_id == 555
    assert work.hold is not None and "poster" in work.hold
    assert ig.calls == []


def test_suzuri_maybe_created_without_id_holds(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = SuzuriError("SUZURI POST /materials failed: ReadTimeout", maybe_created=True)
    with pytest.raises(SuzuriError):
        run(path, FakeInstagram(), FakeSuzuri({2: error}))
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id) == (101, None)
    assert work.hold is not None and "sticker" in work.hold


def test_held_work_is_not_retried(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = SuzuriError("SUZURI POST /materials failed: HTTP 503", maybe_created=True)
    with pytest.raises(SuzuriError):
        run(path, FakeInstagram(), FakeSuzuri({1: error}))
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "nothing to post"
    assert ig.calls == [] and suzuri.created == []


def test_unexpected_exception_during_create_holds(tmp_path):
    path = write(tmp_path, [approved(1)])
    with pytest.raises(OSError):
        run(path, FakeInstagram(), FakeSuzuri({1: OSError("boom")}))
    [work] = load_catalog(path)
    assert work.hold is not None and "poster" in work.hold


def test_suzuri_retry_safe_failure_does_not_hold_and_retries(tmp_path):
    path = write(tmp_path, [approved(1)])
    with pytest.raises(SuzuriError):
        run(path, FakeInstagram(), FakeSuzuri({1: SuzuriError("SUZURI POST /materials failed: HTTP 401")}))
    [work] = load_catalog(path)
    assert work.hold is None and work.suzuri_poster_id is None
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run(path, ig, suzuri) == "posted NO.001"
    assert len(suzuri.created) == 2


def test_ig_maybe_published_holds(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = InstagramError("Instagram POST /1/media_publish failed: ReadTimeout", maybe_published=True)
    with pytest.raises(InstagramError):
        run(path, FakeInstagram(error), FakeSuzuri())
    [work] = load_catalog(path)
    assert (work.suzuri_poster_id, work.suzuri_sticker_id) == (101, 102)
    assert work.ig_media_id is None
    assert work.hold is not None and "instagram" in work.hold
    ig = FakeInstagram()
    assert run(path, ig, FakeSuzuri()) == "nothing to post"
    assert ig.calls == []


def test_expired_token_does_not_hold(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = InstagramError("Instagram POST /1/media failed: HTTP 400, Graph error code 190", code=190)
    with pytest.raises(InstagramError):
        run(path, FakeInstagram(error), FakeSuzuri())
    [work] = load_catalog(path)
    assert work.hold is None


def test_hold_reason_has_no_secret_or_body(tmp_path):
    path = write(tmp_path, [approved(1)])
    error = SuzuriError("SUZURI POST /materials failed: ReadTimeout secret-key-123", maybe_created=True)
    with pytest.raises(SuzuriError):
        run(path, FakeInstagram(), FakeSuzuri({1: error}))
    [work] = load_catalog(path)
    assert "secret-key-123" not in work.hold
    assert "SuzuriError" in work.hold


def test_broken_poster_image_holds_and_uploads_nothing(tmp_path):
    path = write(tmp_path, [approved(1)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()

    def poster_is_blank(img: Image.Image, target: Target) -> list[str]:
        return ["image is blank", "second problem"] if target is Target.POSTER else []

    with pytest.raises(ImageCheckError):
        run(path, ig, suzuri, checker=poster_is_blank)
    assert suzuri.created == [] and ig.calls == []
    [work] = load_catalog(path)
    assert work.hold is not None and "image is blank" in work.hold
    assert "second problem" not in work.hold


def test_real_check_rejects_wrong_sticker_size(tmp_path):
    """Real check_image: the tiny fake image is not 630x630, so the sticker step must stop."""
    path = write(tmp_path, [approved(1, suzuri_poster_id=101)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    with pytest.raises(ImageCheckError):
        run_daily(path, ig, suzuri, TODAY, renderer=tiny_render)
    assert suzuri.created == [] and ig.calls == []
    [work] = load_catalog(path)
    assert "sticker" in work.hold and "size" in work.hold


def test_real_render_and_check_for_sticker(tmp_path):
    """Default renderer and checker end to end, on the cheap sticker only (poster already listed)."""
    path = write(tmp_path, [approved(1, suzuri_poster_id=101)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    assert run_daily(path, ig, suzuri, TODAY) == "posted NO.001"
    [sticker] = suzuri.created
    assert sticker["png"].startswith(b"\x89PNG")


# --- main(): configuration from the environment ---

ENV = {"IG_ACCESS_TOKEN": "tok-abc", "IG_USER_ID": "17841400000000000", "SUZURI_API_KEY": "key-xyz"}


def test_main_reports_missing_variable_by_name(tmp_path, monkeypatch, capsys):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SUZURI_API_KEY")
    assert daily.main(write(tmp_path, [])) == 1
    out = capsys.readouterr()
    assert "SUZURI_API_KEY" in out.err
    assert "tok-abc" not in out.err + out.out


def test_main_prints_result_without_secrets(tmp_path, monkeypatch, capsys):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    assert daily.main(write(tmp_path, [])) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "nothing to post"
    assert not any(value in out.out + out.err for value in ENV.values())


def test_oversized_png_holds_and_uploads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "MAX_PNG_BYTES", 10)  # the tiny fake PNG is larger than this
    path = write(tmp_path, [approved(1)])
    ig, suzuri = FakeInstagram(), FakeSuzuri()
    with pytest.raises(ImageCheckError):
        run(path, ig, suzuri)
    assert suzuri.created == [] and ig.calls == []
    [work] = load_catalog(path)
    assert work.hold is not None and "poster" in work.hold and "limit" in work.hold


def test_stock_ignores_held_works(tmp_path, capsys):
    path = write(tmp_path, [approved(1), approved(2), approved(3, hold="poster: SuzuriError")])
    assert stock.main(path) == 1
    assert "stock low: 2" in capsys.readouterr().out

from typo.caption import build_caption
from typo.catalog import Work

EXPECTED = """MAKE LESS NOISE
静かに、強く。

NO.001
ポスターとステッカーはプロフィールのリンクから。

#typography #タイポグラフィ #graphicdesign #posterdesign #デザイン"""


def make_work() -> Work:
    return Work(
        no=1,
        phrase="MAKE\nLESS\nNOISE",
        ja="静かに、強く。",
        layout="stack",
        accent_line=2,
        created="2026-11-21",
    )


def test_caption_exact() -> None:
    assert build_caption(make_work()) == EXPECTED


def test_caption_limits() -> None:
    caption = build_caption(make_work())
    assert len(caption) <= 2200
    assert sum(1 for word in caption.split() if word.startswith("#")) == 5

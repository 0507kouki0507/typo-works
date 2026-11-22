"""Instagram caption builder."""

from typo.catalog import Work

HASHTAGS = "#typography #タイポグラフィ #graphicdesign #posterdesign #デザイン"
CTA = "ポスターとステッカーはプロフィールのリンクから。"


def build_caption(work: Work) -> str:
    phrase = " ".join(work.phrase.split())
    return f"{phrase}\n{work.ja}\n\nNO.{work.no:03d}\n{CTA}\n\n{HASHTAGS}"

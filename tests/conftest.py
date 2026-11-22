"""Shared image-inspection helpers for the render tests."""

from PIL import Image, ImageChops


def _max_channel_diff(img: Image.Image, color: str | tuple[int, int, int]) -> Image.Image:
    solid = Image.new("RGB", img.size, color)
    r, g, b = ImageChops.difference(img.convert("RGB"), solid).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def ink_bbox_inside_margin(img: Image.Image, ratio: float, tolerance: int = 24) -> bool:
    """True if every pixel that differs from the background lies inside a margin of short side × ratio.

    The background is the top-left corner pixel, so an inverted (ink) ground is judged as ground.
    """
    background = img.convert("RGB").getpixel((0, 0))
    diff = _max_channel_diff(img, background)
    bbox = diff.point(lambda v: 255 if v > tolerance else 0).getbbox()
    if bbox is None:
        return False
    margin = int(min(img.size) * ratio)
    left, top, right, bottom = bbox
    width, height = img.size
    return left >= margin and top >= margin and right <= width - margin and bottom <= height - margin


def count_pixels_near(img: Image.Image, color: str | tuple[int, int, int], tolerance: int = 40) -> int:
    """Number of pixels whose every channel is within `tolerance` of `color`."""
    near = _max_channel_diff(img, color).point(lambda v: 255 if v <= tolerance else 0)
    return near.histogram()[255]

"""Hover boxes (#54): where the kiosk is told each bird is, against where it is drawn."""

from __future__ import annotations

import pytest
from PIL import Image

from fugleramme.render import collage
from fugleramme.render.packing import LAYOUTS

RESOLUTION = (1280, 800)


@pytest.fixture
def entries(tmp_path):
    """Six species off one plate, so every box is the same aspect."""
    art = tmp_path / "bird.png"
    Image.new("RGBA", (200, 150), (40, 40, 40, 255)).save(art)
    return [(f"Genus species{n}", art) for n in range(6)]


def test_a_box_is_named_for_every_bird_that_is_drawn(entries):
    found = collage.regions(entries, RESOLUTION)
    assert [name for name, _ in found] == [name for name, _ in entries]


def test_no_box_leaves_the_page(entries):
    for name, (x0, y0, x1, y1) in collage.regions(entries, RESOLUTION):
        assert 0 <= x0 < x1 <= RESOLUTION[0], name
        assert 0 <= y0 < y1 <= RESOLUTION[1], name


def test_a_box_covers_the_ink_it_is_named_for(entries):
    """The only check that matters: point at the middle of a box and the pixel
    under it belongs to that bird, not to bare paper."""
    page = collage.render_collage(entries, RESOLUTION, textured=False)
    paper = page.getpixel((0, 0))
    for name, (x0, y0, x1, y1) in collage.regions(entries, RESOLUTION):
        middle = ((x0 + x1) // 2, (y0 + y1) // 2)
        assert page.getpixel(middle) != paper, f"{name} at {middle} is bare paper"


def test_names_off_gives_boxes_of_the_birds_alone(entries):
    """With no label reserved, a box is the sprite - so every one of them shrinks."""
    with_names = dict(collage.regions(entries, RESOLUTION, show_names=True))
    without = dict(collage.regions(entries, RESOLUTION, show_names=False))
    assert with_names.keys() == without.keys()
    for name in with_names:
        tall = with_names[name][3] - with_names[name][1]
        assert tall > without[name][3] - without[name][1]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_layout_reports_boxes(entries, layout):
    assert len(collage.regions(entries, RESOLUTION, layout=layout)) == len(entries)


def test_a_page_with_nothing_drawable_has_no_boxes():
    assert collage.regions([("Genus species", None)], RESOLUTION) == []

"""Layout invariants: whichever packer the admin picks, opaque pixels never
overlap and nothing runs off the page - and the page is a function of the pick
and of the manifest, so neither is answered from the cache."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from fugleramme import names
from fugleramme.names import MANIFEST, MAX_SCALE, scale_of
from fugleramme.render import packing
from fugleramme.render.collage import _page, _Sprite, render_collage
from fugleramme.render.packing import LAYOUTS


def _sprites(count: int = 8) -> list[_Sprite]:
    """Birds of a few sizes, largest first, as _layout hands them to a packer."""
    return [
        _Sprite(n, 90 - 8 * n, np.ones((90 - 8 * n, 70 - 6 * n), dtype=bool)) for n in range(count)
    ]


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_packed_page_never_overlaps_and_never_clips(layout):
    width, height = 500, 400
    placed = LAYOUTS[layout].pack(_sprites(), width, height)
    assert placed is not None

    occupied = np.zeros((height, width), dtype=bool)
    for sprite, x, y in placed:
        h, w = sprite.mask.shape
        assert x >= 0 and y >= 0 and x + w <= width and y + h <= height
        assert not (occupied[y : y + h, x : x + w] & sprite.mask).any()
        occupied[y : y + h, x : x + w] |= sprite.mask


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_set_that_cannot_fit_is_reported_rather_than_squeezed(layout):
    assert LAYOUTS[layout].pack(_sprites(4), 100, 60) is None


def test_the_layout_is_part_of_the_collage_cache_key(crowded):
    pages = [
        np.asarray(render_collage(crowded(6), (500, 400), show_names=False, layout=layout))
        for layout in ("spiral", "voids")
    ]
    assert not np.array_equal(*pages)  # the cache would have served the first twice


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_a_lone_bird_is_not_shrunk_by_the_size_search(crowded, layout):
    """The size search bisects between a fit and a failure. A set that fits at
    full size has no failure to bisect against."""
    page = render_collage(crowded(1), (800, 600), show_names=False, layout=layout)
    drawn = (np.asarray(page.convert("L")) < 200).sum()
    assert drawn > 800 * 600 * 0.2  # a fifth of the page, not a stamp in the middle


def test_the_grid_collision_test_agrees_with_the_pixels():
    board = packing.Board(240, 180)
    mask = packing._pool(np.ones((37, 53), dtype=bool))
    board.take(mask, 3, 4)  # cells are the packer's unit; the promise is about pixels
    legal = board.legal(mask, 53, 37)

    fine = np.zeros((180, 240), dtype=bool)
    fine[3 * packing.K : 3 * packing.K + 37, 4 * packing.K : 4 * packing.K + 53] = True
    for y, x in zip(*legal.nonzero(), strict=True):
        py, px = int(y) * packing.K, int(x) * packing.K
        assert not fine[py : py + 37, px : px + 53].any()


@pytest.fixture
def styled(tmp_path):
    """Two identical plates in a style, and the manifest that scales them. Two
    files, because a scale belongs to a plate rather than to a species."""
    birds = tmp_path / "birds"
    birds.mkdir()
    paths = []
    for n in range(2):
        path = birds / f"plate-{n}.png"
        Image.new("RGBA", (200, 150), (40, 40, 40, 255)).save(path)
        paths.append(path)

    def build(scale: float | None):
        listed = {} if scale is None else {"birds/plate-1.png": {"scale": str(scale)}}
        (tmp_path / MANIFEST).write_text(json.dumps(listed))
        names._manifests.clear()  # mtime-cached, and two writes can share a tick
        return [("Genus first", paths[0]), ("Genus second", paths[1])]

    return build


def _dims(entries) -> list[int]:
    """Each bird's packed size, in the order it was handed over."""
    page = _page(entries, (800, 600), False, "", "medium", str, "spiral", 0.04)
    return [p.dim for p in sorted(page.placed, key=lambda p: p.index)]


def test_an_unscaled_plate_is_sized_by_its_species_alone(styled):
    assert len(set(_dims(styled(None)))) == 1  # same mass, same file, same size


@pytest.mark.parametrize("scale", [1.6, 0.5])
def test_a_scaled_plate_is_drawn_by_that_much_against_its_neighbour(styled, scale):
    plain, scaled = _dims(styled(scale))
    # The set shrinks to fit as a whole, so it is the ratio that holds, not the px.
    assert scaled / plain == pytest.approx(scale, rel=0.02)


def test_a_scale_is_part_of_the_collage_cache_key(styled):
    before = _dims(styled(None))
    assert _dims(styled(2.0))[1] > before[1]  # the cache would have served the first


def test_a_scale_past_the_bounds_is_clamped_rather_than_obeyed(styled):
    entries = styled(400)
    assert scale_of(entries[1][1]) == MAX_SCALE


def test_an_unreadable_scale_leaves_the_plate_alone(styled):
    entries = styled("huge")
    assert scale_of(entries[1][1]) == 1.0

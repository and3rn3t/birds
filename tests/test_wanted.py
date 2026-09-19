"""The plate-cutting queue: what this station hears that the style cannot draw.

The fake's own species are all drawable by the shipped artwork, so these build a
thin style instead - which is also the honest shape of the problem, since every
station hears birds nobody has cut a plate for yet.
"""

from __future__ import annotations

import pytest
from PIL import Image

from fugleramme import wanted
from fugleramme.languages import NONE, SCIENTIFIC

HEARD = [
    ("Turdus merula", 9),
    ("Parus major", 4),
    ("Myotis daubentonii", 3),  # a bat: the station classifies those too
    ("Pica pica", 1),
]


@pytest.fixture
def style(tmp_path):
    """A style that can draw whichever species are named, and nothing else."""

    def build(*names: str) -> tuple:
        birds = tmp_path / "thin" / "birds"
        birds.mkdir(parents=True, exist_ok=True)
        for name in names:
            Image.new("RGBA", (8, 8)).save(birds / f"{name.lower().replace(' ', '-')}.png")
        return tmp_path, "thin"

    return build


class _Station:
    """Just the one call `heard` makes."""

    def __init__(self, counted):
        self.counted = counted
        self.windows = []

    def species_since(self, hours=24):
        self.windows.append(hours)
        return self.counted


def test_a_station_is_asked_for_its_whole_record_by_default():
    station = _Station(HEARD)
    wanted.heard(station)
    assert station.windows == [0]  # "0 or less is not a window"


def test_only_birds_are_worth_cutting_a_plate_for():
    assert ("Myotis daubentonii", 3) not in wanted.heard(_Station(HEARD))


def test_a_window_is_passed_through_when_one_is_asked_for():
    station = _Station(HEARD)
    wanted.heard(station, 168)
    assert station.windows == [168]


def test_what_the_style_draws_is_not_wanted(style):
    images_dir, name = style("Turdus merula", "Pica pica")
    counted = wanted.heard(_Station(HEARD))
    assert wanted.undrawn(counted, images_dir, name) == [("Parus major", 4)]


def test_the_order_is_the_detector_s_own(style):
    """Most heard first: the top of the list is the plate seen most often."""
    images_dir, name = style()
    counted = wanted.heard(_Station(HEARD))
    assert wanted.undrawn(counted, images_dir, name) == counted


def test_a_style_that_draws_everything_asks_for_nothing(style):
    images_dir, name = style("Turdus merula", "Parus major", "Pica pica")
    counted = wanted.heard(_Station(HEARD))
    assert wanted.undrawn(counted, images_dir, name) == []
    assert "Nothing to cut." in wanted.report(counted, [], None)[-1]


def test_a_station_with_nothing_heard_yet_is_said_so_rather_than_tabulated():
    assert wanted.report([], [], None) == ["no birds heard yet: nothing to want"]


def test_the_listing_counts_what_is_drawn_against_what_was_heard():
    counted = wanted.heard(_Station(HEARD))
    lines = wanted.report(counted, [("Parus major", 4)], None)
    assert lines[0] == "2 of the 3 birds heard here can be drawn."
    assert lines[-1].split() == ["4", "Parus", "major"]


def test_a_scientific_frame_gets_no_second_column():
    """A name column repeating the species column says nothing."""
    missing = [("Parus major", 4)]
    lines = wanted.report(missing, missing, None)
    assert lines[2].split() == ["heard", "species"]
    assert lines[-1].endswith("Parus major")


def test_a_named_frame_gets_the_common_name_beside_the_species():
    missing = [("Parus major", 4)]
    lines = wanted.report(missing, missing, lambda name: "Great Tit")
    assert lines[-1].endswith("Great Tit")


@pytest.mark.parametrize("language", [SCIENTIFIC, NONE])
def test_a_frame_labelled_in_latin_is_not_given_a_latin_name_column(
    detector, style, capsys, language
):
    url, _httpd = detector(count=20, seed=0)
    images_dir, name = style("Turdus merula")  # a style has to draw something to be one
    assert wanted.run(url, "", "", images_dir, images_dir, name, language, 0) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].startswith(url)
    columns = next(line for line in printed if line.strip().startswith("heard"))
    assert columns.split() == ["heard", "species"]


def test_a_detector_that_cannot_be_reached_is_a_failure_not_an_empty_queue(style):
    """An empty list would read as "nothing to cut" - the opposite of the truth."""
    images_dir, name = style("Turdus merula")
    assert (
        wanted.run("http://127.0.0.1:1", "", "", images_dir, images_dir, name, SCIENTIFIC, 0) == 1
    )


def test_a_frame_with_no_artwork_at_all_says_so(tmp_path):
    assert wanted.run("http://127.0.0.1:1", "", "", tmp_path, tmp_path, "", SCIENTIFIC, 0) == 1


def test_the_queue_reads_off_a_real_detector(source, style):
    """End to end over /api/v2, against the fake's generated record."""
    images_dir, name = style()
    counted = wanted.heard(source(count=40, seed=0))
    assert counted and all(count > 0 for _, count in counted)
    assert wanted.undrawn(counted, images_dir, name) == counted  # the style draws none

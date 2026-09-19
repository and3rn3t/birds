"""Which plates are worth cutting for this station.

    fugleramme-wanted                                  # the saved connection
    fugleramme-wanted --detector http://pi.local:8090  # someone else's
    fugleramme-wanted --hours 168                      # only what a week held

The frame draws a few hundred of BirdNET's several thousand species, and that
gap closes one hand-cut plate at a time (`tools/add_bird.py`). This says which
ones are worth cutting here: every bird this station has actually heard that the
active style cannot draw, most heard first. The service already knows them -
`modes.artless` logs the window's once, on change - but only for the window on
the glass, and only to the journal.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from . import languages, taxa
from .api import ApiSource
from .config import DEFAULT_CONFIG_PATH, DEFAULT_DETECTOR_URL, REPO_ROOT
from .names import drawable_keys, normalize, resolve
from .settings import SettingsStore, from_env
from .source import Source, Unavailable


def heard(source: Source, hours: float = 0) -> list[tuple[str, int]]:
    """Birds this station has counted, most heard first. A station also
    classifies bats, frogs and noise; none of those is a plate to cut."""
    return [(name, count) for name, count in source.species_since(hours) if taxa.is_bird(name)]


def undrawn(counted: list[tuple[str, int]], images_dir: Path, style: str) -> list[tuple[str, int]]:
    """Of those, the ones the style has no artwork for, in the order given - so
    the top of the list is the plate that would hang on the wall most often."""
    drawable = drawable_keys(images_dir, style)
    return [(name, count) for name, count in counted if normalize(name) not in drawable]


def report(
    counted: list[tuple[str, int]],
    missing: list[tuple[str, int]],
    label: Callable[[str], str] | None,
) -> list[str]:
    """The listing, as lines. `label` gives a bird's common name, or None when
    the frame is set to scientific names and the column would say nothing."""
    if not counted:
        return ["no birds heard yet: nothing to want"]
    drawn = len(counted) - len(missing)
    head = f"{drawn} of the {len(counted)} birds heard here can be drawn."
    if not missing:
        return [head, "", "Every one of them. Nothing to cut."]

    # The scientific name is what `tools/add_bird.py --species` wants, so it is
    # the column that must not be abbreviated.
    width = max(len(name) for name, _ in missing)
    lines = [head, "", f"{'heard':>7}  {'species':<{width}}  {'name' if label else ''}".rstrip()]
    for name, count in missing:
        common = label(name) if label else ""
        lines.append(f"{count:>7}  {name:<{width}}  {common}".rstrip())
    return lines


def run(
    url: str,
    username: str,
    password: str,
    cache_dir: Path,
    images_dir: Path,
    requested_style: str,
    language: str,
    hours: float,
) -> int:
    source = ApiSource(url, username, password)
    languages.use(source)
    style = resolve(requested_style, images_dir)
    if not style:
        print(f"no artwork in {images_dir}", file=sys.stderr)
        return 1

    try:
        counted = heard(source, hours)
    except Unavailable as error:
        print(f"{url}: {error}", file=sys.stderr)
        return 1

    # In the frame's own words: a station whose labels are scientific gets no
    # second column rather than a blank one.
    namer = languages.namer(language, languages.NONE, cache_dir)
    label = None if language in (languages.SCIENTIFIC, languages.NONE) else namer.inline
    window = "all time" if hours <= 0 else f"the last {hours:g} hours"
    print(f"{url}  -  style {style}  -  {window}\n")
    print("\n".join(report(counted, undrawn(counted, images_dir, style), label)))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detector", help=f"base URL (default: saved, or {DEFAULT_DETECTOR_URL})")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--images", type=Path, default=REPO_ROOT / "assets" / "artwork", help="artwork directory"
    )
    parser.add_argument("--style", default="", help="style to check (default: the saved one)")
    parser.add_argument(
        "--hours", type=float, default=0, help="window to count over (default: all time)"
    )
    args = parser.parse_args()

    # Same seeds as the service, so this reports the frame's own detector.
    saved = SettingsStore(args.config, from_env()).get()
    sys.exit(
        run(
            args.detector or saved.detector_url,
            args.username or saved.detector_username,
            args.password or saved.detector_password,
            args.config.parent,
            args.images,
            args.style or saved.style,
            saved.primary_language,
            args.hours,
        )
    )


if __name__ == "__main__":
    main()

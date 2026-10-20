"""Tests for the checks on hand edits, and the reason codes they send."""

import pytest

from scmp3.app.job_fields import field_problems, render_problem
from scmp3.core.suggest import InvalidOverride


@pytest.mark.parametrize("year", ["1", "999", "2024", "9999", " 2024 "])
def test_years_from_1_to_9999_are_fine(year: str) -> None:
    assert field_problems({"year": year}) == {}


# Full-width digits are digits to Python, but not to the tag rendering.
FULL_WIDTH_YEAR = "".join(chr(0xFF10 + int(digit)) for digit in "2024")


@pytest.mark.parametrize("year", ["0", "0000", "10000", "20x4", "-1", FULL_WIDTH_YEAR, "2024.0"])
def test_anything_else_is_not_a_year(year: str) -> None:
    assert field_problems({"year": year}) == {"year": {"reason": "NOT_A_YEAR"}}


@pytest.mark.parametrize("track", ["1", "999", "3/12", "3 / 12", "12/12"])
def test_track_numbers_and_totals_up_to_999_are_fine(track: str) -> None:
    assert field_problems({"track_no": track}) == {}


@pytest.mark.parametrize("track", ["0", "1000", "3/1000", "three", "3/", "/12", "0/5"])
def test_anything_else_is_not_a_track(track: str) -> None:
    assert field_problems({"track_no": track}) == {"track_no": {"reason": "NOT_A_TRACK"}}


def test_a_total_smaller_than_the_track_is_refused() -> None:
    assert field_problems({"track_no": "5/3"}) == {"track_no": {"reason": "TOTAL_BEFORE_TRACK"}}


def test_a_blank_number_clears_its_field() -> None:
    assert field_problems({"year": "  ", "track_no": ""}) == {}


def test_text_fields_take_anything() -> None:
    assert field_problems({"title": "", "artist": "  Kaia ", "genre": "20x4"}) == {}


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("year", {"year": {"reason": "NOT_A_YEAR"}}),
        ("track_no", {"track_no": {"reason": "NOT_A_TRACK"}}),
        ("track_total", {"track_no": {"reason": "TOTAL_BEFORE_TRACK"}}),
    ],
)
def test_a_refusal_from_rendering_goes_next_to_the_field_that_was_typed(
    field: str, expected: dict[str, object]
) -> None:
    assert render_problem(InvalidOverride(field, "x")) == expected

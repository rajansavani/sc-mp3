"""Tests for ScmpError: its kind, its detail and the folder a disk error was about."""

import pytest

from scmp3.core.engine import HttpStatusError
from scmp3.core.errors import ErrorKind, ScmpError


def test_a_misspelled_kind_is_refused_where_it_is_raised() -> None:
    with pytest.raises(ValueError):
        ScmpError("dsik")


def test_the_text_is_the_kind_and_the_detail() -> None:
    assert str(ScmpError(ErrorKind.disk, "No space left")) == "disk: No space left"
    assert str(ScmpError(ErrorKind.drm)) == "drm"


def test_an_error_says_no_folder_unless_given_one() -> None:
    assert ScmpError(ErrorKind.disk).where is None
    assert ScmpError(ErrorKind.disk, "full", where="data").where == "data"


def test_subclasses_start_without_a_folder() -> None:
    error = HttpStatusError(ErrorKind.service_down, "HTTP 503", status=503, answer={})
    assert error.where is None

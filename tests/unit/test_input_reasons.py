"""Tests for the codes add_inputs sends in place of core's reasons."""

import logging
import re
from pathlib import Path

import pytest

from scmp3.app.input_reasons import REASON_CODES, REASONS_BY_MODULE, reason_code
from scmp3.core import pipeline, urls

CORE = Path(urls.__file__).parent
# A reason raised by name, as in `raise UnsupportedInput(SOUNDCLOUD_NOT_TRACK)`.
RAISED_REASON = re.compile(r"UnsupportedInput\(([A-Z][A-Z_]+)\)")


def test_each_code_is_its_constants_name() -> None:
    for module, names in REASONS_BY_MODULE.items():
        for name in names:
            assert REASON_CODES[getattr(module, name)] == name


def test_every_reason_core_raises_by_name_has_a_code() -> None:
    raised = {
        name
        for path in CORE.rglob("*.py")
        for name in RAISED_REASON.findall(path.read_text(encoding="utf-8"))
    }
    assert raised
    assert raised <= set(REASON_CODES.values())


def test_every_reason_the_pipeline_gives_for_later_inputs_has_a_code() -> None:
    assert set(pipeline.NOT_YET.values()) <= set(REASON_CODES)


def test_a_reason_without_a_code_is_sent_as_not_supported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="scmp3.app.input_reasons"):
        assert reason_code("Some new reason.") == "NOT_SUPPORTED"
    assert "has no code" in caplog.text

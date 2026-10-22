"""The base every model shares, which converts it to a JSON-safe dict and back.

Dates become ISO strings, paths become strings and enums become their values.
"""

import functools
from collections.abc import Callable, Mapping
from dataclasses import fields
from datetime import date, datetime
from enum import Enum
from pathlib import Path, PurePath
from types import NoneType, UnionType
from typing import Any, Self, Union, get_args, get_origin, get_type_hints

# How to rebuild each type from its JSON form. Matched exactly, since a datetime is also a date.
_LOADERS: dict[type, Callable[[Any], Any]] = {
    datetime: datetime.fromisoformat,
    date: date.fromisoformat,
    Path: Path,
    float: float,
}


class Model:
    """Base for the model dataclasses. Converts them to JSON-safe dicts and back."""

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a dict that json.dumps accepts."""
        return {f.name: _dump(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild from `to_dict` output. Missing keys get defaults; unknown keys are ignored."""
        types = field_types(cls)
        return cls(
            **{name: _load(hint, data[name]) for name, hint in types.items() if name in data}
        )


@functools.cache
def field_types(cls: type) -> dict[str, Any]:
    """Each dataclass field of `cls` with its resolved type hint."""
    hints = get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


def _dump(value: Any) -> Any:
    """Turn one field value into its JSON form."""
    if isinstance(value, Model):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    # This covers datetime too, since it's a subclass of date.
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, list | tuple):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _load(hint: Any, value: Any) -> Any:
    """Turn a JSON value back into the type its field declares."""
    if value is None:
        return None
    origin = get_origin(hint)
    if origin is UnionType or origin is Union:
        return _load(optional_type(hint), value)
    if origin is list:
        (item_type,) = get_args(hint)
        return [_load(item_type, item) for item in value]
    if origin is dict:
        _, item_type = get_args(hint)
        return {key: _load(item_type, item) for key, item in value.items()}
    # Literal values are kept as they are.
    if not isinstance(hint, type):
        return value
    if issubclass(hint, Model):
        return hint.from_dict(value)
    if issubclass(hint, Enum):
        return hint(value)
    loader = _LOADERS.get(hint)
    return loader(value) if loader else value


def optional_type(hint: Any) -> Any:
    """The X in `X | None`. Models use no other kind of union."""
    options = [arg for arg in get_args(hint) if arg is not NoneType]
    if len(options) != 1:
        raise TypeError(f"Models only support `X | None` unions, not {hint}")
    return options[0]

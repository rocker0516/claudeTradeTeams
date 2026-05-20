"""Dataclass <-> JSON serialization helpers for SQLite persistence.

Used primarily by SQLiteEventLog, which stores arbitrary frozen
dataclass events as JSON text. The encoder handles:

  - primitive types (str, int, bool, float)
  - Decimal     → {"__decimal__": "<str>"}
  - datetime    → {"__datetime__": "<isoformat>"}
  - str-Enum    → enum.value
  - nested dataclasses (recursive)
  - Optional[T] / Union[T, None]

Not supported (will fail at encode or decode time):
  - non-str enums
  - generic containers (list[T], dict[K, V], tuple[T, ...]) — not needed
    for current runtime event shapes, but easy to extend later

Why text-encode Decimal: SQLite REAL is double-precision float, lossy
for financial values. Storing as text round-trips exactly.

Why __decimal__/__datetime__ markers: distinguishes a decoded Decimal
from a regular dict, so we can rehydrate the right type on read.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar, Union

T = TypeVar("T")

_DECIMAL_MARKER = "__decimal__"
_DATETIME_MARKER = "__datetime__"


def encode_dataclass(obj: object) -> str:
    """Serialize a frozen dataclass to a JSON string."""
    if not dataclasses.is_dataclass(obj):
        raise TypeError(f"encode_dataclass requires a dataclass instance, got {type(obj).__name__}")
    return json.dumps(_to_jsonable(obj))


def decode_dataclass(target: type[T], payload: str) -> T:
    """Deserialize a JSON payload back to an instance of `target`."""
    return _from_jsonable(target, json.loads(payload))  # type: ignore[no-any-return]


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return {_DECIMAL_MARKER: str(obj)}
    if isinstance(obj, datetime):
        return {_DATETIME_MARKER: obj.isoformat()}
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    return obj


def _from_jsonable(target: Any, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict) and _DECIMAL_MARKER in value:
        return Decimal(value[_DECIMAL_MARKER])
    if isinstance(value, dict) and _DATETIME_MARKER in value:
        return datetime.fromisoformat(value[_DATETIME_MARKER])

    # Resolve Optional[T] / Union[T, None]
    origin = typing.get_origin(target)
    if origin is Union:
        non_none = [a for a in typing.get_args(target) if a is not type(None)]
        if len(non_none) == 1:
            return _from_jsonable(non_none[0], value)
        for arg in non_none:
            try:
                return _from_jsonable(arg, value)
            except Exception:
                continue
        return value

    # str-Enum
    if isinstance(target, type) and issubclass(target, Enum):
        return target(value)

    # Nested dataclass
    if isinstance(target, type) and dataclasses.is_dataclass(target):
        hints = typing.get_type_hints(target)
        kwargs = {
            f.name: _from_jsonable(hints[f.name], value[f.name]) for f in dataclasses.fields(target)
        }
        return target(**kwargs)

    return value

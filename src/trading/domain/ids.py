"""Identifier and symbol newtypes.

NewType gives mypy enough information to catch mix-ups (e.g. passing
a Symbol where an OrderId is expected) without runtime overhead.
"""

from typing import NewType

Symbol = NewType("Symbol", str)
"""Trading pair symbol, e.g. Symbol('BTCUSDT')."""

OrderId = NewType("OrderId", str)
"""Exchange-assigned order ID."""

ClientOrderId = NewType("ClientOrderId", str)
"""Locally-generated idempotency key for an order. Must be unique per submission attempt."""

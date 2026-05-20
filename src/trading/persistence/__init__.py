"""Persistence layer — append-only journals + event/alert logs.

Four narrow ABCs (one per aggregate root) keep components decoupled:

  - OrderJournal     (used by OMS)
  - AccountJournal   (used by PMS)
  - EventLog         (used by Supervisor + stateful CircuitBreakers)
  - AlertLog         (used by AlertRouter)

A single concrete impl (e.g. SQLitePersistence) typically implements
all four, but each consumer depends only on the narrow interface it
needs (Interface Segregation).

Core property of all four: APPEND-ONLY. No UPDATE, no DELETE.
Reconstruction on restart works by replaying the log.
"""

from .account_journal import AccountJournal
from .alert_log import AlertLog
from .event_log import EventLog
from .order_journal import OrderJournal

__all__ = ["AccountJournal", "AlertLog", "EventLog", "OrderJournal"]

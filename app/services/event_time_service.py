from __future__ import annotations

from datetime import UTC, datetime

from app.db.models import PoopEvent


def event_occurrence_time(event: PoopEvent) -> datetime | None:
    """Use recorded occurrence time; retain legacy interpretation until reviewed."""
    value = event.created_at if event.source is None else event.occurred_at
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value

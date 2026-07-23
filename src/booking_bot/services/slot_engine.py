from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, order=True, slots=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Time intervals must use timezone-aware datetimes")
        if self.end <= self.start:
            raise ValueError("Interval end must be after its start")

    def overlaps(self, other: "TimeInterval") -> bool:
        return self.start < other.end and other.start < self.end


def generate_slots(
    availability: list[TimeInterval],
    busy: list[TimeInterval],
    *,
    duration: timedelta,
    step: timedelta,
    buffer_before: timedelta = timedelta(0),
    buffer_after: timedelta = timedelta(0),
    earliest_start: datetime | None = None,
    latest_start: datetime | None = None,
) -> list[TimeInterval]:
    """Return service intervals that fit fully into the available calendar.

    Buffers participate in conflict detection but are not included in the returned
    client-facing service interval.
    """

    if duration <= timedelta(0):
        raise ValueError("Duration must be positive")
    if step <= timedelta(0):
        raise ValueError("Step must be positive")
    if buffer_before < timedelta(0) or buffer_after < timedelta(0):
        raise ValueError("Buffers cannot be negative")
    for boundary in (earliest_start, latest_start):
        if boundary is not None and boundary.tzinfo is None:
            raise ValueError("Slot boundaries must use timezone-aware datetimes")

    slots: set[TimeInterval] = set()
    ordered_busy = sorted(busy)

    for window in sorted(availability):
        candidate_start = window.start + buffer_before
        while candidate_start + duration + buffer_after <= window.end:
            if earliest_start is not None and candidate_start < earliest_start:
                candidate_start += step
                continue
            if latest_start is not None and candidate_start > latest_start:
                break

            service_interval = TimeInterval(candidate_start, candidate_start + duration)
            occupied_interval = TimeInterval(
                candidate_start - buffer_before,
                candidate_start + duration + buffer_after,
            )
            if not any(occupied_interval.overlaps(blocked) for blocked in ordered_busy):
                slots.add(service_interval)
            candidate_start += step

    return sorted(slots)

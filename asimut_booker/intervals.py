"""Pure integer-minute interval operations.

All intervals are half-open ranges, ``[start, end)``.  Touching intervals do
not overlap: a booking ending at 10:00 does not overlap one starting at 10:00,
although a policy such as the same-room gap rule may still reject it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MINUTES_PER_DAY = 24 * 60


@dataclass(frozen=True, order=True, slots=True)
class Interval:
    """A non-empty, same-day half-open interval measured in integer minutes."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or isinstance(self.start, bool):
            raise TypeError("interval start must be an integer minute")
        if not isinstance(self.end, int) or isinstance(self.end, bool):
            raise TypeError("interval end must be an integer minute")
        if not 0 <= self.start < self.end <= MINUTES_PER_DAY:
            raise ValueError(
                f"invalid interval [{self.start}, {self.end}); expected "
                f"0 <= start < end <= {MINUTES_PER_DAY}"
            )

    @property
    def duration(self) -> int:
        """Return the interval duration in minutes."""

        return self.end - self.start

    def overlaps(self, other: "Interval") -> bool:
        """Return whether this interval intersects ``other``."""

        return self.start < other.end and other.start < self.end

    def touches(self, other: "Interval") -> bool:
        """Return whether the intervals meet at exactly one boundary."""

        return self.end == other.start or other.end == self.start

    def contains(self, other: "Interval") -> bool:
        """Return whether this interval fully contains ``other``."""

        return self.start <= other.start and other.end <= self.end

    def intersection(self, other: "Interval") -> "Interval | None":
        """Return the intersection with ``other``, or ``None``."""

        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return Interval(start, end) if start < end else None

    def expand(
        self,
        before: int = 0,
        after: int = 0,
        *,
        lower: int = 0,
        upper: int = MINUTES_PER_DAY,
    ) -> "Interval":
        """Expand the interval while clipping it to the supplied bounds."""

        if before < 0 or after < 0:
            raise ValueError("expansion amounts must be non-negative")
        if not 0 <= lower < upper <= MINUTES_PER_DAY:
            raise ValueError("invalid expansion bounds")
        start = max(lower, self.start - before)
        end = min(upper, self.end + after)
        return Interval(start, end)


def merge_intervals(
    intervals: Iterable[Interval], *, merge_touching: bool = True
) -> list[Interval]:
    """Return sorted, merged intervals.

    Touching ranges are merged by default because this is normally the useful
    representation for occupied or unavailable time.
    """

    ordered = sorted(intervals)
    if not ordered:
        return []

    merged: list[Interval] = [ordered[0]]
    for current in ordered[1:]:
        previous = merged[-1]
        should_merge = current.start < previous.end or (
            merge_touching and current.start == previous.end
        )
        if should_merge:
            merged[-1] = Interval(previous.start, max(previous.end, current.end))
        else:
            merged.append(current)
    return merged


def subtract_intervals(base: Interval, blocked: Iterable[Interval]) -> list[Interval]:
    """Subtract every blocked range from ``base``.

    The result is sorted, non-overlapping, and contains no zero-length ranges.
    Blocks outside ``base`` are harmless.
    """

    relevant: list[Interval] = []
    for block in merge_intervals(blocked):
        intersection = base.intersection(block)
        if intersection is not None:
            relevant.append(intersection)

    if not relevant:
        return [base]

    available: list[Interval] = []
    cursor = base.start
    for block in relevant:
        if cursor < block.start:
            available.append(Interval(cursor, block.start))
        cursor = max(cursor, block.end)
        if cursor >= base.end:
            break
    if cursor < base.end:
        available.append(Interval(cursor, base.end))
    return available


def subtract_from_many(
    available: Iterable[Interval], blocked: Iterable[Interval]
) -> list[Interval]:
    """Subtract ``blocked`` ranges from a collection of available intervals."""

    merged_blocks = merge_intervals(blocked)
    result: list[Interval] = []
    for interval in merge_intervals(available):
        result.extend(subtract_intervals(interval, merged_blocks))
    return result


def floor_to_increment(value: int, increment: int = 15) -> int:
    """Round a minute value down to a positive increment."""

    if increment <= 0:
        raise ValueError("increment must be positive")
    return (value // increment) * increment


def ceil_to_increment(value: int, increment: int = 15) -> int:
    """Round a minute value up to a positive increment."""

    if increment <= 0:
        raise ValueError("increment must be positive")
    return ((value + increment - 1) // increment) * increment

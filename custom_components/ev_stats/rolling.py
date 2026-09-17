"""A rolling window with a median and an honest coverage figure.

Pure, like `trapezoid.py` and `classify.py`.

The median rather than the mean because the thing being measured - what the
house draws when the car is not charging - is a baseline with occasional
enormous excursions. An oven, a kettle and a heat pump all land in the same
24-hour window as hours of 0.3 kW standby, and a mean would let one dinner
raise the baseline enough to swallow a whole charging session's evidence.

`coverage` is the part worth keeping. A window that has only been filling for
half an hour will happily report a median, and that median will be confidently
wrong; the classifier needs to be able to ask how much of the window is
actually populated so it can decline to answer instead.
"""

from __future__ import annotations

from collections import deque
from statistics import median


class RollingWindow:
    """Timestamped samples, aged out, summarised by their median."""

    def __init__(self, max_age_seconds: float, max_samples: int) -> None:
        self._max_age = float(max_age_seconds)
        # A cap as well as an age, because a 1 Hz power meter over 24 hours is
        # 86,400 samples and the median is sorted on every read.
        self._samples: deque[tuple[float, float]] = deque(maxlen=max_samples)

    def __len__(self) -> int:
        return len(self._samples)

    def add(self, timestamp: float, value: float) -> None:
        """Record a sample.

        Out-of-order timestamps are dropped rather than inserted. They arrive
        from a recorder backfill racing the live feed, and the only thing the
        window uses order for is ageing, so an older sample appended to the
        right would be expired against the wrong edge.
        """
        if self._samples and timestamp < self._samples[-1][0]:
            return
        self._samples.append((timestamp, value))
        self._expire(timestamp)

    def _expire(self, now: float) -> None:
        cutoff = now - self._max_age
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def median(self, now: float | None = None) -> float | None:
        """The middle sample, or None when there is nothing to say."""
        if now is not None:
            self._expire(now)
        if not self._samples:
            return None
        return median(value for _ts, value in self._samples)

    def coverage(self, now: float | None = None) -> float:
        """How much of the window the samples actually span, 0.0 to 1.0.

        Oldest to newest, with `now` passed in so that ageing runs first. That
        ordering is what makes this decay when a source goes quiet: the oldest
        samples fall out of the window while no new ones arrive at the other
        end, so the span shrinks from both sides and a feed that died twelve
        hours ago reports half a window rather than a full one.

        Measuring from the oldest sample to `now` instead would not decay at
        all - expiry pins the oldest edge to the cutoff, so a long-dead feed
        would report complete coverage until its last sample aged out.
        """
        if now is not None:
            self._expire(now)
        if len(self._samples) < 2:
            return 0.0
        span = self._samples[-1][0] - self._samples[0][0]
        return min(1.0, max(0.0, span / self._max_age))

    def clear(self) -> None:
        self._samples.clear()

"""Measuring the usable pack from a charge that spanned a wide SoC range.

Pure, and small, and the only battery-health signal most cars can actually
produce.

The obvious sensor is usually worthless for this. A "range at full charge"
figure is typically computed by the vehicle integration as capacity divided by
recent consumption, so the implied capacity comes back as whatever capacity was
configured - it restates driving style and nothing else. Most cars expose no
state of health, no cycle count and no pack capacity at all.

What is measurable: every charge spanning a wide SoC range is an independent
capacity measurement, `kWh at the wall x efficiency / (dSoC / 100)`.
Degradation is a downward *trend* in that series, and the efficiency term
cancels out of a trend even though it is assumed - a constant factor cannot
create a slope.

Ambient temperature belongs with each measurement because it is the confound
that actually limits this. A cold pack charges less efficiently, so a winter
session reads low with no degradation at all, and that seasonal swing can
exceed the roughly 2% a year being looked for. Comparing like-for-like months
year on year is what removes it.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import fmean

# Below this a measurement is noise. A charge covering 8% of the pack divides
# by 0.08, so a 1% error in either reading becomes a 12% error in the answer.
MIN_SOC_DELTA = Decimal(30)

# One session is far too noisy to move a converged figure on its own: real
# measurements scatter around 1.5% against a signal of about 2% a year. The
# published number follows the mean of the most recent few.
ROLLING_WINDOW = 8


def implied_capacity(
    kwh: Decimal,
    soc_delta: Decimal,
    efficiency: Decimal,
) -> Decimal | None:
    """Usable pack size implied by one charge, or None if it cannot say.

    None rather than a number whenever the measurement is not defensible. A
    narrow charge produces an answer that looks exactly as authoritative as a
    good one, and there is no way to tell them apart afterwards.
    """
    if soc_delta < MIN_SOC_DELTA:
        return None
    if kwh <= 0 or efficiency <= 0:
        return None
    return (kwh * efficiency / (soc_delta / Decimal(100))).quantize(Decimal("0.01"))


def rolling_mean(values: list[float], window: int = ROLLING_WINDOW) -> float | None:
    """The mean of the most recent `window` measurements."""
    recent = [v for v in values[-window:] if v is not None]
    if not recent:
        return None
    return round(fmean(recent), 2)


def trend_per_year(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope through (days, kWh), scaled to a year.

    The number people actually want from this series, and the one a single
    reading cannot give. Returned only with enough points spread over enough
    time to mean anything: a slope through four measurements taken in one month
    extrapolates a season into a decade.
    """
    if len(points) < 4:
        return None
    span = max(x for x, _ in points) - min(x for x, _ in points)
    if span < 180:
        return None
    mean_x = fmean(x for x, _ in points)
    mean_y = fmean(y for _, y in points)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return round(numerator / denominator * 365, 3)

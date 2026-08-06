"""Expand a planned figure into actual data points.

The model describes a figure's *shape* -- a handful of numbers -- and this fills
in the observations. Two reasons not to have the model emit the points directly:
a constrained decode over sixty floats is slow and error-prone on a small model,
and models produce suspiciously tidy data. Hand-written points sitting exactly
on the regression line is the tell that gives the whole thing away.

Seeded off the figure label, so rebuilding the same spec gives the same chart.
"""

from __future__ import annotations

import random

from .plan import PlannedFigure
from .schema import Figure

POINTS_PER_SCATTER = 30
POINTS_PER_BOX = 14
POINTS_PER_LINE = 10


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n == 1:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


def expand(planned: PlannedFigure) -> Figure:
    rng = random.Random(planned.label)
    common = {
        "kind": planned.kind,
        "label": planned.label,
        "caption": planned.caption,
        "xlabel": planned.xlabel,
        "ylabel": planned.ylabel,
    }
    spread = abs(planned.spread) or 0.1

    if planned.kind == "bar_errorbar":
        return Figure(
            **common,
            categories=planned.categories,
            values=planned.values,
            # Vary the error bars a little; identical bars on every column looks
            # designed rather than measured.
            errors=[round(spread * rng.uniform(0.6, 1.4), 3) for _ in planned.values],
        )

    if planned.kind == "box":
        return Figure(
            **common,
            categories=planned.categories,
            groups=[
                [round(rng.gauss(median, spread), 3) for _ in range(POINTS_PER_BOX)]
                for median in planned.values
            ],
        )

    if planned.kind == "scatter_regression":
        y_lo, y_hi = planned.values[0], planned.values[-1]
        xs = _linspace(planned.x_min, planned.x_max, POINTS_PER_SCATTER)
        span = planned.x_max - planned.x_min or 1.0
        ys = [
            round(y_lo + (y_hi - y_lo) * ((x - planned.x_min) / span) + rng.gauss(0, spread), 3)
            for x in xs
        ]
        return Figure(**common, x=[round(x, 3) for x in xs], y=ys)

    xs = _linspace(planned.x_min, planned.x_max, POINTS_PER_LINE)
    series: dict[str, list[float]] = {}
    for i, name in enumerate(planned.categories):
        level = planned.values[i] if i < len(planned.values) else planned.values[-1]
        # Fan the series apart so they do not sit on top of each other.
        drift = spread * (i - (len(planned.categories) - 1) / 2)
        series[name] = [
            round(level + drift * (j / max(len(xs) - 1, 1)) + rng.gauss(0, spread * 0.5), 3)
            for j in range(len(xs))
        ]
    return Figure(**common, x=[round(x, 3) for x in xs], series=series)

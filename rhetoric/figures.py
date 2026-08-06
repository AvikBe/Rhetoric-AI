"""Fixed chart templates.

A small model asked to write matplotlib freely produces code that crashes. So it
never writes code -- it picks a `kind` and fills in numbers, and the plotting
lives here where it can be tested. Adding a chart type means adding a function
and a key to FIGURE_REQUIREMENTS, not loosening the schema.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from .schema import Figure

# Serif to match the Times body text; muted greys so nothing looks like a
# marketing deck.
STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "font.size": 9,
    "axes.linewidth": 0.8,
    "axes.edgecolor": "#333333",
    "axes.grid": True,
    "grid.color": "#dddddd",
    "grid.linewidth": 0.5,
    "figure.dpi": 200,
}

PALETTE = ["#3b5b7f", "#a4593d", "#5f7a52", "#7a5f7f", "#8a7f3d"]


def _bar_errorbar(ax, fig: Figure) -> None:
    ax.bar(
        fig.categories,
        fig.values,
        yerr=fig.errors,
        color=PALETTE[0],
        edgecolor="#1f1f1f",
        linewidth=0.6,
        capsize=3,
        error_kw={"elinewidth": 0.8, "ecolor": "#1f1f1f"},
    )
    ax.set_axisbelow(True)


def _scatter_regression(ax, fig: Figure) -> None:
    ax.scatter(fig.x, fig.y, s=14, color=PALETTE[0], alpha=0.75, edgecolor="none")
    n = len(fig.x)
    if n >= 2:
        # Least squares by hand rather than pulling in numpy for four lines.
        mx = sum(fig.x) / n
        my = sum(fig.y) / n
        denom = sum((xi - mx) ** 2 for xi in fig.x)
        if denom:
            slope = sum((xi - mx) * (yi - my) for xi, yi in zip(fig.x, fig.y)) / denom
            intercept = my - slope * mx
            lo, hi = min(fig.x), max(fig.x)
            ax.plot(
                [lo, hi],
                [slope * lo + intercept, slope * hi + intercept],
                color="#a4593d",
                linewidth=1.2,
                linestyle="--",
            )
    ax.set_axisbelow(True)


def _box(ax, fig: Figure) -> None:
    # Not `labels=`/`tick_labels=`: the keyword was renamed in matplotlib 3.9 and
    # the old name removed later, so setting the ticks afterwards works on both.
    bp = ax.boxplot(fig.groups, patch_artist=True, widths=0.5)
    ax.set_xticks(range(1, len(fig.categories) + 1))
    ax.set_xticklabels(fig.categories)
    for patch in bp["boxes"]:
        patch.set_facecolor(PALETTE[0])
        patch.set_alpha(0.65)
        patch.set_edgecolor("#1f1f1f")
        patch.set_linewidth(0.7)
    for part in ("whiskers", "caps", "medians"):
        for line in bp[part]:
            line.set_color("#1f1f1f")
            line.set_linewidth(0.7)
    ax.set_axisbelow(True)


def _line(ax, fig: Figure) -> None:
    for i, (name, ys) in enumerate(fig.series.items()):
        ax.plot(
            fig.x,
            ys,
            label=name,
            color=PALETTE[i % len(PALETTE)],
            linewidth=1.3,
            marker="o",
            markersize=3,
        )
    if len(fig.series) > 1:
        ax.legend(frameon=False, fontsize=8)
    ax.set_axisbelow(True)


RENDERERS = {
    "bar_errorbar": _bar_errorbar,
    "scatter_regression": _scatter_regression,
    "box": _box,
    "line": _line,
}


def render(fig: Figure, outdir: Path) -> Path:
    plt.rcParams.update(STYLE)
    f, ax = plt.subplots(figsize=(5.2, 3.1))
    RENDERERS[fig.kind](ax, fig)
    ax.set_xlabel(fig.xlabel)
    ax.set_ylabel(fig.ylabel)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    f.tight_layout(pad=0.4)

    path = outdir / f"{fig.label}.png"
    f.savefig(path, bbox_inches="tight")
    plt.close(f)
    return path


def render_all(figures: list[Figure], outdir: Path) -> list[Path]:
    return [render(fig, outdir) for fig in figures]

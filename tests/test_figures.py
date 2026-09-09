"""Chart rendering and the figure-data synthesiser."""

from __future__ import annotations

from pathlib import Path

import pytest

from rhetoric import figures, synth
from rhetoric.plan import PlannedFigure
from rhetoric.schema import Figure
from tests.conftest import figure as planned


class TestEveryKindRenders:
    """Regression: `boxplot(labels=)` was removed from matplotlib.

    Only the bar chart was exercised by the reference spec, so the box kind
    crashed the first time anything used it.
    """

    @pytest.mark.parametrize(
        "spec",
        [
            planned("bar_errorbar", "bars", ["a", "b", "c"], [8.1, 7.6, 2.1]),
            planned("box", "boxes", ["a", "b"], [8.0, 2.4], spread=0.6),
            planned("scatter_regression", "scatter", [], [2.0, 8.4], x_min=0, x_max=90),
            planned("line", "lines", ["one", "two"], [7.0, 6.4], x_min=0, x_max=9),
        ],
        ids=["bar_errorbar", "box", "scatter_regression", "line"],
    )
    def test_renders_a_png(self, spec: dict, tmp_path: Path) -> None:
        fig = synth.expand(PlannedFigure.model_validate(spec))
        path = figures.render(fig, tmp_path)
        assert path.exists() and path.stat().st_size > 1000
        assert path.name == f"{spec['label']}.png"


class TestSynthesis:
    def test_is_deterministic(self) -> None:
        """Seeded off the label, so rebuilding a spec gives the same chart."""
        spec = PlannedFigure.model_validate(
            planned("scatter_regression", "scatter", [], [2.0, 8.0], x_min=0, x_max=10)
        )
        assert synth.expand(spec).y == synth.expand(spec).y

    def test_different_labels_differ(self) -> None:
        a = planned("scatter_regression", "one", [], [2.0, 8.0], x_min=0, x_max=10)
        b = dict(a, label="two")
        assert (
            synth.expand(PlannedFigure.model_validate(a)).y
            != synth.expand(PlannedFigure.model_validate(b)).y
        )

    def test_scatter_has_noise_around_the_fit(self) -> None:
        """Hand-written points sitting exactly on the line is the tell."""
        spec = PlannedFigure.model_validate(
            planned("scatter_regression", "scatter", [], [2.0, 8.0], x_min=0, x_max=10, spread=0.5)
        )
        fig = synth.expand(spec)
        exact = [2.0 + (8.0 - 2.0) * (x / 10) for x in fig.x]
        residuals = [abs(y - e) for y, e in zip(fig.y, exact)]
        assert sum(residuals) / len(residuals) > 0.1
        assert fig.y != exact

    def test_error_bars_vary(self) -> None:
        """Identical error bars on every column look designed, not measured."""
        spec = PlannedFigure.model_validate(
            planned("bar_errorbar", "bars", ["a", "b", "c"], [8.0, 7.0, 2.0])
        )
        assert len(set(synth.expand(spec).errors)) > 1

    def test_line_series_are_named(self) -> None:
        spec = PlannedFigure.model_validate(
            planned("line", "lines", ["alpha", "beta"], [7.0, 6.0], x_min=0, x_max=9)
        )
        fig = synth.expand(spec)
        assert set(fig.series) == {"alpha", "beta"}
        assert all(len(v) == len(fig.x) for v in fig.series.values())

    def test_expanded_figure_satisfies_the_render_schema(self) -> None:
        spec = PlannedFigure.model_validate(
            planned("box", "boxes", ["a", "b"], [8.0, 2.4], spread=0.5)
        )
        assert isinstance(synth.expand(spec), Figure)


class TestPlannedFigureValidation:
    def test_category_and_value_counts_must_match(self) -> None:
        with pytest.raises(ValueError, match="categories"):
            PlannedFigure.model_validate(
                planned("bar_errorbar", "bars", ["a", "b", "c"], [1.0, 2.0])
            )

    def test_flat_chart_is_rejected(self) -> None:
        """Regression: a live paper plotted three visually identical bars.

        0.92 / 0.95 / 0.89 with spread 0.04 -- the differences were smaller than
        the error bars, so the figure showed nothing at all.
        """
        with pytest.raises(ValueError, match="no contrast"):
            PlannedFigure.model_validate(
                planned("bar_errorbar", "flat", ["a", "b", "c"], [0.92, 0.95, 0.89], spread=0.04)
            )

    def test_negative_control_gives_contrast(self) -> None:
        spec = planned("bar_errorbar", "ok", ["a", "b", "ctrl"], [7.8, 7.6, 2.1], spread=0.4)
        assert PlannedFigure.model_validate(spec).values[-1] == 2.1

    def test_single_value_needs_no_contrast(self) -> None:
        PlannedFigure.model_validate(planned("bar_errorbar", "one", ["a"], [5.0], spread=1.0))

    def test_line_needs_series_names(self) -> None:
        with pytest.raises(ValueError, match="no series names"):
            PlannedFigure.model_validate(planned("line", "lines", [], [1.0]))

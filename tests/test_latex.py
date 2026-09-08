"""The escaping layer.

Every test here is a bug that actually reached a broken PDF or a failed build.
"""

from __future__ import annotations

import pytest

from rhetoric.latex import Latex, body, cite_keys, escape, finalize, ident, pdf_string, to_latex


class TestEscaping:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("50% of trials", r"50\% of trials"),
            ("Smith & Jones", r"Smith \& Jones"),
            ("cost $5", r"cost \$5"),
            ("snake_case", r"snake\_case"),
            ("#1", r"\#1"),
            ("{braces}", r"\{braces\}"),
        ],
    )
    def test_specials(self, raw: str, expected: str) -> None:
        assert escape(raw) == expected

    def test_backslash_does_not_double_escape(self) -> None:
        """Single pass: a replacement must not be re-escaped by a later rule."""
        assert escape("a\\b") == r"a\textbackslash{}b"


class TestTypography:
    def test_closing_quote_before_punctuation(self) -> None:
        """A closing quote is usually followed by punctuation.

        The original lookahead heuristic read `"hydration".` as an *opening*
        quote because `.` is non-whitespace, producing ``hydration`` in the PDF.
        """
        assert to_latex('At 50 percent "hydration".') == "At 50 percent ``hydration''."

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ('He said "yes" and left.', "He said ``yes'' and left."),
            ('("bracketed") end', "(``bracketed'') end"),
            ("Ellipsis... and more.", "Ellipsis\\ldots{} and more."),
        ],
    )
    def test_directional_quotes(self, raw: str, expected: str) -> None:
        assert to_latex(raw) == expected

    def test_unicode_from_models(self) -> None:
        assert to_latex("an em—dash and a “curly” quote") == (
            "an em---dash and a ``curly'' quote"
        )


class TestIdentifiers:
    def test_underscore_is_rejected(self) -> None:
        """Regression: `\\label{fig:a\\_b}` killed the build.

        Labels reach LaTeX inside \\csname, where an escaped underscore becomes a
        control sequence and the build dies with "Missing \\endcsname inserted" --
        pointing at the .aux file, nowhere near the actual cause.
        """
        with pytest.raises(ValueError, match="unsafe identifier"):
            ident("fig:a_b")

    def test_safe_identifier_passes_through_unescaped(self) -> None:
        result = ident("fig:sci-by-form")
        assert result == "fig:sci-by-form"
        assert isinstance(result, Latex)

    def test_finalize_leaves_identifiers_alone(self) -> None:
        """Latex-marked strings must survive the escape-everything hook."""
        assert finalize(ident("sci-by-form")) == "sci-by-form"
        assert finalize("sci_by_form") == r"sci\_by\_form"


class TestCitations:
    def test_marker_becomes_citep(self) -> None:
        assert body("Prior work [[smith2019]] disagrees.") == (
            r"Prior work \citep{smith2019} disagrees."
        )

    def test_multiple_keys_in_one_marker(self) -> None:
        assert body("See [[a2019, b2020]].") == r"See \citep{a2019,b2020}."

    def test_marker_survives_escaping(self) -> None:
        """Escaping runs first; brackets are not special, so markers stay intact."""
        assert body("Costs 50% [[a2019]].") == r"Costs 50\% \citep{a2019}."

    def test_cite_keys_extraction(self) -> None:
        assert cite_keys("[[a2019]] and [[b2020, c2021]]") == ["a2019", "b2020", "c2021"]


class TestPdfMetadata:
    def test_strips_markup_and_non_ascii(self) -> None:
        """hyperref writes these into the PDF catalog verbatim."""
        assert pdf_string("Café {braces} \\cmd") == "Cafe braces cmd"

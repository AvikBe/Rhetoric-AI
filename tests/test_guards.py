"""The leakage guards.

These exist because a model was asked politely and did not comply. They are also
the easiest thing in the project to get wrong in the *other* direction, so the
false-positive cases matter as much as the true ones.
"""

from __future__ import annotations

import pytest

from rhetoric.guards import (
    EXEMPLAR_TOKENS,
    PROMPTS,
    SHINGLE,
    _exemplars,
    assert_no_latex,
    assert_original,
    find_borrowed_phrases,
    find_copied,
)


def _prompt_text() -> str:
    return " ".join(p.read_text(encoding="utf-8") for p in PROMPTS.glob("*.md"))


class TestTokensStayHonest:
    @pytest.mark.parametrize("token", EXEMPLAR_TOKENS)
    def test_every_token_appears_in_a_prompt(self, token: str) -> None:
        """A token the model is never shown can only fire on a coincidence.

        The list once held nouns from specs/cereal_soup.json -- a reference
        output, not a prompt input. Three live runs in a row then died because
        the model independently coined the "Journal of Culinary Ontology",
        which is simply what a paper on food taxonomy would cite. Banning an
        invention is worse than missing a copy: the retry loop cannot fix it,
        because there is nothing to fix.
        """
        assert token in _prompt_text(), (
            f"{token!r} is in EXEMPLAR_TOKENS but in no prompt, so it can only "
            "reject an invention. Remove it, or put it in a prompt."
        )

    def test_a_natural_coinage_is_not_leakage(self) -> None:
        """The exact false positive that cost three runs."""
        assert find_copied("Journal of Culinary Ontology, 14(2), 88-113") == []
        assert find_copied("M. Okonkwo-Reyes, Institute for Culinary Taxonomies") == []

    def test_a_real_copy_is_still_caught(self) -> None:
        assert find_copied("measured on cleaved feldspar using the Hoyle scale") == [
            "Hoyle scale",
            "feldspar",
        ]


class TestBorrowedPhrases:
    def test_a_whole_example_sentence_is_rejected(self) -> None:
        """Verbatim reuse is leakage at any length."""
        sentence = next(e for e in _exemplars() if 8 <= len(e.split()) < SHINGLE)
        assert find_borrowed_phrases(f"We note that {sentence} Accordingly, we proceed.")

    def test_a_long_span_is_rejected(self) -> None:
        long_example = max(_exemplars(), key=lambda e: len(e.split()))
        span = " ".join(long_example.split()[:SHINGLE])
        assert find_borrowed_phrases(f"In our case {span} and so on.")

    def test_a_short_shared_tail_is_allowed(self) -> None:
        """Adapting the register is the point; only wholesale reuse is not.

        At an eight-word window this was rejected, and one Limitations section
        failed four attempts running on the same construction, taking the whole
        run down with it.
        """
        adapted = (
            "A replication using kerbside rather than laboratory samples would "
            "be informative, and we have not conducted one."
        )
        assert find_borrowed_phrases(adapted) == []

    def test_texts_are_checked_separately(self) -> None:
        """A window must not straddle two unrelated fields.

        Passing one concatenated blob invented phrases that exist in neither
        string, and the verdict then depended on which serializer built it.
        """
        assert find_borrowed_phrases("ends with the word", "scale was calibrated") == []


class TestNoLatex:
    @pytest.mark.parametrize("bad", [r"\textbf{x}", r"$\alpha$", r"{\bf x}"])
    def test_markup_is_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="contains LaTeX"):
            assert_no_latex(f"Some prose {bad} more prose.", "paragraph")

    @pytest.mark.parametrize(
        "fine", ["p < 0.001", "Cohen's kappa of 0.83", "the 95% interval", "a_b in prose"]
    )
    def test_ordinary_prose_passes(self, fine: str) -> None:
        assert_no_latex(fine, "paragraph")


class TestAssertOriginal:
    def test_reports_what_to_change(self) -> None:
        with pytest.raises(ValueError, match="feldspar"):
            assert_original("The feldspar comparison holds throughout.")

    def test_clean_text_passes(self) -> None:
        assert_original("A hot dog satisfies every structural criterion.")

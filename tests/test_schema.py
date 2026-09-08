"""PaperSpec validators.

These encode the safety properties: a generated paper must never carry an
identifier or DOI that could point at somebody's real work.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rhetoric.schema import Citation, Figure, PaperSpec


class TestArxivIdCannotBeReal:
    @pytest.mark.parametrize("real", ["2408.12345v1", "2401.00001", "1912.99999v2"])
    def test_rejects_plausible_ids(self, plan_dict: dict, real: str) -> None:
        """A resolvable-looking ID could collide with somebody's real submission."""
        with pytest.raises(ValidationError, match="could collide with a real paper"):
            _spec(plan_dict, arxiv_id=real)

    @pytest.mark.parametrize("impossible", ["2699.99999v1", "2400.12345", "9999.00001v3"])
    def test_accepts_impossible_months(self, plan_dict: dict, impossible: str) -> None:
        assert _spec(plan_dict, arxiv_id=impossible).arxiv_id == impossible

    def test_rejects_malformed(self, plan_dict: dict) -> None:
        with pytest.raises(ValidationError, match="must look like"):
            _spec(plan_dict, arxiv_id="not-an-id")


class TestDoiIsUnresolvable:
    def test_real_prefix_is_rewritten(self) -> None:
        """10.0000 is reserved and never resolves."""
        c = _citation(doi="10.1038/nature12373")
        assert c.doi == "10.0000/nature12373"

    def test_bare_suffix_gets_the_prefix(self) -> None:
        assert _citation(doi="abc.123").doi == "10.0000/abc.123"

    def test_none_stays_none(self) -> None:
        assert _citation(doi=None).doi is None


class TestIdentifierCharsets:
    def test_figure_label_rejects_underscore(self) -> None:
        with pytest.raises(ValidationError, match="no underscores"):
            Figure(kind="bar_errorbar", label="a_b", caption="c", categories=["x"],
                   values=[1.0], errors=[0.1])

    def test_citation_key_must_be_alphanumeric(self) -> None:
        with pytest.raises(ValidationError, match="must be alphanumeric"):
            _citation(key="smith_2019")


class TestFigureKindRequirements:
    @pytest.mark.parametrize(
        ("kind", "kwargs"),
        [
            ("bar_errorbar", {"categories": ["a"], "values": [1.0]}),  # no errors
            ("scatter_regression", {"x": [1.0, 2.0]}),  # no y
            ("box", {"categories": ["a"]}),  # no groups
            ("line", {"x": [1.0]}),  # no series
        ],
    )
    def test_missing_data_for_kind(self, kind: str, kwargs: dict) -> None:
        with pytest.raises(ValidationError, match="is missing"):
            Figure(kind=kind, label="fig", caption="c", **kwargs)


class TestProseCitations:
    def test_undefined_key_is_rejected(self, plan_dict: dict) -> None:
        """Otherwise it renders as a silent bold `?` in the PDF."""
        with pytest.raises(ValidationError, match="undefined keys"):
            _spec(plan_dict, abstract="Cites [[ghost2020]] which does not exist.")


# ---------------------------------------------------------------- helpers


def _citation(**overrides) -> Citation:
    base = {
        "key": "adeyemi2022",
        "short": "Adeyemi",
        "authors": "Adeyemi, R.",
        "title": "t",
        "venue": "v",
        "year": 2022,
    }
    return Citation.model_validate(base | overrides)


def _spec(plan_dict: dict, **overrides) -> PaperSpec:
    base = {
        "claim": plan_dict["claim"],
        "field": plan_dict["field"],
        "title": plan_dict["title"],
        "authors": [{"name": "A", "affiliation": "B"}],
        "date": "3 Aug 2026",
        "abstract": "An abstract.",
        "keywords": ["k"],
        "dataset": plan_dict["dataset"],
        "sections": [{"heading": "Introduction", "paragraphs": ["A paragraph."]}],
        "citations": plan_dict["citations"],
    }
    return PaperSpec.model_validate(base | overrides)

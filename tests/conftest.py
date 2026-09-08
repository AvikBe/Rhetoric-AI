"""Shared fixtures.

`plan_dict` is the smallest thing that validates, so a test can corrupt one
field and assert on that field alone.
"""

from __future__ import annotations

import pytest

from rhetoric.plan import PaperPlan

PARAGRAPH = (
    "The classification is contested despite the absence of any principled "
    "criterion excluding it. We formalise membership as a three-part conjunction "
    "and evaluate it against a validated instrument. The result holds at rates "
    "indistinguishable from an uncontested member of the category."
)


def figure(kind: str, label: str, categories: list[str], values: list[float], **kw) -> dict:
    return {
        "kind": kind,
        "label": label,
        "caption": "Mean index by form. The negative control does not claim membership.",
        "xlabel": kw.get("xlabel", ""),
        "ylabel": "SCI (1-9)",
        "categories": categories,
        "values": values,
        "spread": kw.get("spread", 0.4),
        "x_min": kw.get("x_min", 0.0),
        "x_max": kw.get("x_max", 0.0),
    }


@pytest.fixture
def plan_dict() -> dict:
    return {
        "claim": "A hot dog is a sandwich",
        "field": "structural gastronomy",
        "title": "Load-Bearing Bread: A Structural Argument",
        "short_title": "Load-Bearing Bread",
        "authors": [
            {
                "name": "R. Adeyemi",
                "affiliation": "Institute for Applied Sandwich Mechanics",
                "email": "r.adeyemi@iasm.example",
            }
        ],
        "keywords": ["structure", "bread"],
        "dataset": {
            "n": 812,
            "population": "delicatessen patrons",
            "instrument": "Structural Containment Index (SCI)",
            "key_stat": "p < 0.001",
            "effect_size": "d = 1.42",
        },
        "abstract_beats": ["State the criterion and report it holds [[adeyemi2022]]."],
        "sections": [
            {"heading": "Introduction", "beats": ["Frame the question."], "figure": None},
            {"heading": "Methods", "beats": ["Define containment."], "figure": None},
            {
                "heading": "Results",
                "beats": ["Report rates.", "Note the control was excluded."],
                "figure": figure(
                    "bar_errorbar", "sci-by-form", ["Sub", "Hot dog", "Bread"], [8.1, 7.6, 2.1]
                ),
            },
            {"heading": "Conclusion", "beats": ["Restate flatly."], "figure": None},
        ],
        "citations": [
            {
                "key": "adeyemi2022",
                "short": "Adeyemi",
                "authors": "Adeyemi, R.",
                "title": "Containment and the sandwich boundary",
                "venue": "Journal of Structural Gastronomy",
                "year": 2022,
                "volume": "9(2)",
                "pages": "31--58",
                "doi": "10.0000/jsg.2022.0092",
            }
        ],
    }


@pytest.fixture
def plan(plan_dict: dict) -> PaperPlan:
    return PaperPlan.model_validate(plan_dict)

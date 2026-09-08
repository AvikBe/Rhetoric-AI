"""Checks that make prompt instructions enforceable.

Everything here exists because a model was asked politely and did not comply.
Shared by every generation stage, so a rule added once applies to all of them.
"""

from __future__ import annotations

import re

# Few-shot examples get copied, not imitated. Given a complete worked example,
# qwen3:8b returned a hot dog paper written by the cereal exemplar's authors, at
# the cereal exemplar's institutions, with its sample size, effect size and
# citation keys intact -- while the prompt was explicitly telling it not to reuse
# any of them. Asking louder does not work; checking does.
#
# Add any distinctive proper noun or number you put in a prompt or reference spec.
EXEMPLAR_TOKENS = (
    # People and places from specs/cereal_soup.json
    "Okonkwo-Reyes", "Lindqvist", "Beaumont", "Whitmore", "Breakfast Dynamics",
    # Citation keys and journals
    "vandermeer", "fitzgerald2023", "marchetti2019", "nakamura2022", "okonkwo2021",
    "Applied Gastronomy", "Culinary Ontology", "Food Physics Letters",
    "Empirical Foodways", "Comestible Philosophy",
    # Statistics that would otherwise be reused verbatim
    "1247", "1.84", "89.2",
    # Subject matter
    "gazpacho", "Gazpacho", "bisque", "Bisque", "Chilled Suspension", "CSPI", "soup-ness",
)


def find_copied(text: str) -> list[str]:
    return sorted({token for token in EXEMPLAR_TOKENS if token in text})


def assert_original(text: str) -> None:
    """Raise if generated output reuses the few-shot example's specifics."""
    if found := find_copied(text):
        raise ValueError(
            f"copied from the example: {found}. Invent your own authors, "
            "institutions, sample size, statistics and references."
        )


# The model writes plain prose; latex.py adds every backslash. A model that
# starts emitting \textbf or \cite produces literal backslashes in the PDF,
# because the escaper faithfully escapes them.
_LATEX_ISH = re.compile(r"\\[a-zA-Z]+|\$\$?[^$]+\$\$?|\{\\")


def assert_no_latex(text: str, where: str) -> None:
    if found := _LATEX_ISH.findall(text):
        raise ValueError(
            f"{where} contains LaTeX ({found[:3]}). Write plain prose only; "
            "cite with [[key]] markers and let the renderer handle formatting."
        )

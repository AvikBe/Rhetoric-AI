"""Checks that make prompt instructions enforceable.

Everything here exists because a model was asked politely and did not comply.
Shared by every generation stage, so a rule added once applies to all of them.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"

# Few-shot examples get copied, not imitated. Given a complete worked example,
# qwen3:8b returned a hot dog paper written by the cereal exemplar's authors, at
# the cereal exemplar's institutions, with its sample size, effect size and
# citation keys intact -- while the prompt was explicitly telling it not to reuse
# any of them. Asking louder does not work; checking does.
#
# Add any distinctive proper noun or number you put in a prompt or reference spec.
EXEMPLAR_TOKENS = (
    # People and places from specs/cereal_soup.json. Bare surnames as well as
    # citation keys: deepseek-v4-flash invented "Marchetti & van der Heijden
    # (2018)", which slipped past a list that only held `marchetti2019`.
    "Okonkwo-Reyes", "Lindqvist", "Beaumont", "Whitmore", "Breakfast Dynamics",
    "Marchetti", "Nakamura", "Fitzgerald", "van der Meer",
    # Citation keys and journals
    "vandermeer", "fitzgerald2023", "marchetti2019", "nakamura2022", "okonkwo2021",
    "Applied Gastronomy", "Culinary Ontology", "Food Physics Letters",
    "Empirical Foodways", "Comestible Philosophy",
    # Statistics that would otherwise be reused verbatim
    "1247", "1.84", "89.2",
    # Subject matter
    "gazpacho", "Gazpacho", "bisque", "Bisque", "Chilled Suspension", "CSPI", "soup-ness",
)

# Length of the word window used to detect verbatim reuse of a prompt example.
# Eight is specific enough that ordinary academic phrasing does not trip it, and
# short enough to catch a single borrowed clause.
SHINGLE = 8


def find_copied(*texts: str) -> list[str]:
    joined = " ".join(texts)
    return sorted({token for token in EXEMPLAR_TOKENS if token in joined})


def _normalise(text: str) -> str:
    """Lowercase words separated by single spaces.

    Punctuation becomes a space rather than being deleted. Deleting it fused
    words across JSON field boundaries -- compact `model_dump_json` output ran
    `"claim to.","xlabel":"Item"` together into `claim toxlabelitem`, silently
    destroying every shingle near a boundary and making the check depend on
    which serializer produced the string.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


@lru_cache(maxsize=1)
def _exemplar_shingles() -> dict[str, str]:
    """Word windows from the register examples, mapped back to their source.

    Derived from the prompt files rather than hand-listed, so the ban cannot
    drift out of sync when the examples are edited.
    """
    shingles: dict[str, str] = {}
    for path in sorted(PROMPTS.glob("*.user.md")):
        for quoted in re.findall(r'^\s*-\s+"(.+?)"\s*$', path.read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL):
            words = _normalise(quoted).split()
            for i in range(len(words) - SHINGLE + 1):
                shingles[" ".join(words[i : i + SHINGLE])] = " ".join(quoted.split())
    return shingles


def find_borrowed_phrases(*texts: str) -> list[str]:
    """Register examples reproduced word-for-word rather than imitated.

    Each text is windowed separately, so a shingle can never span two unrelated
    fields and invent a match that neither field contains.
    """
    haystack: set[str] = set()
    for text in texts:
        words = _normalise(text).split()
        haystack |= {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}
    return sorted({source for key, source in _exemplar_shingles().items() if key in haystack})


def assert_original(*texts: str) -> None:
    """Raise if generated output reuses the few-shot example's specifics.

    Two distinct failures, both seen in live runs: reusing the example's names
    and numbers, and reproducing its sentences word-for-word. The second is the
    subtler one -- every paper comes out carrying the same handful of lines.
    """
    if found := find_copied(*texts):
        raise ValueError(
            f"copied from the example: {found}. Invent your own authors, "
            "institutions, sample size, statistics and references."
        )
    if borrowed := find_borrowed_phrases(*texts):
        raise ValueError(
            f"reproduced an example sentence verbatim: {borrowed[0][:90]!r}. "
            "The examples show register only -- write your own sentences."
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

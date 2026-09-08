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

# Two separate tests, because one threshold cannot serve both jobs.
#
# A whole example sentence reproduced verbatim is always leakage, however short:
# "This is unfortunate, because the question is tractable." is eight words and
# was appearing in every paper.
#
# A long *span* is leakage even when the sentence around it differs. But a short
# shared tail is legitimate imitation -- adapting "a replication using X rather
# than Y would be informative, and we have not conducted one" to a new subject
# keeps a nine-word tail, and that is the register doing its job. At eight words
# this rejected the adaptation and the Limitations section failed four attempts
# running on the same construction.
SHINGLE = 12


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
def _exemplars() -> list[str]:
    """The register examples, normalised, read from the prompt files.

    Derived rather than hand-listed, so the ban cannot drift out of sync when
    the examples are edited.
    """
    quoted: list[str] = []
    for path in sorted(PROMPTS.glob("*.user.md")):
        text = path.read_text(encoding="utf-8")
        quoted += re.findall(r'^\s*-\s+"(.+?)"\s*$', text, re.MULTILINE | re.DOTALL)
    return [_normalise(q) for q in quoted]


@lru_cache(maxsize=1)
def _exemplar_shingles() -> set[str]:
    windows: set[str] = set()
    for words in (e.split() for e in _exemplars()):
        windows |= {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}
    return windows


def find_borrowed_phrases(*texts: str) -> list[str]:
    """Overlaps with the register examples that count as reuse, not imitation.

    Returns the offending words themselves rather than the example they came
    from: the model has to be told which clause to rewrite, and a whole source
    sentence does not say that when only part of it was reused.

    Each text is windowed separately, so a span can never straddle two unrelated
    fields and invent a match neither of them contains.
    """
    found: set[str] = set()
    for text in texts:
        normalised = _normalise(text)
        # A full example sentence, at any length.
        found |= {e for e in _exemplars() if e and e in normalised}

        # Or a long span of one.
        words = normalised.split()
        for i in range(len(words) - SHINGLE + 1):
            window = " ".join(words[i : i + SHINGLE])
            if window in _exemplar_shingles():
                found.add(window)
    return sorted(found)


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
            f"this reuses wording from the register examples: {borrowed[0][:110]!r}. "
            "Rewrite that clause in your own words. Imitating the shape of the "
            "examples is fine; repeating their wording is not."
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

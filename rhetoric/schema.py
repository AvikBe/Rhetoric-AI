"""Typed shape of a paper.

This module is the contract between the model and the renderer. Every field here
is something a constrained decode has to fill in, so keep the shapes flat and the
names obvious -- small models follow a schema much better when the field names
already say what goes in them.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FigureKind = Literal["bar_errorbar", "scatter_regression", "box", "line"]

# How heavily the document announces itself. "subtle" keeps the disclosure in
# places a real preprint already uses, so the page still reads as a paper;
# "loud" adds the diagonal watermark and a boxed banner over the top.
Disclosure = Literal["subtle", "loud"]

# Fields each figure kind actually needs. Everything else on Figure stays empty.
FIGURE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "bar_errorbar": ("categories", "values", "errors"),
    "scatter_regression": ("x", "y"),
    "box": ("categories", "groups"),
    "line": ("x", "series"),
}

_ARXIV_ID = re.compile(r"^(\d{2})(\d{2})\.(\d{4,5})(v\d+)?$")


class Author(BaseModel):
    name: str
    affiliation: str
    email: str | None = None


class Figure(BaseModel):
    """One chart.

    Deliberately a single wide model rather than a union: a small model picks a
    `kind` and fills the matching fields far more reliably than it navigates a
    discriminated union.
    """

    kind: FigureKind
    label: str  # slug; becomes both the PNG filename and the LaTeX \label
    caption: str
    xlabel: str = ""
    ylabel: str = ""

    categories: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    errors: list[float] = Field(default_factory=list)
    x: list[float] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)
    groups: list[list[float]] = Field(default_factory=list)
    series: dict[str, list[float]] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _slug(cls, v: str) -> str:
        # No underscores: this string goes into \label, \ref and a graphics
        # filename, none of which survive LaTeX escaping. See latex.ident.
        if not re.fullmatch(r"[a-z0-9-]+", v):
            raise ValueError(f"figure label must be [a-z0-9-]+ (no underscores), got {v!r}")
        return v

    @model_validator(mode="after")
    def _kind_has_its_data(self) -> Figure:
        missing = [f for f in FIGURE_REQUIREMENTS[self.kind] if not getattr(self, f)]
        if missing:
            raise ValueError(f"figure {self.label!r} of kind {self.kind!r} is missing {missing}")
        return self


class Section(BaseModel):
    heading: str
    paragraphs: list[str]
    figure: Figure | None = None


class Dataset(BaseModel):
    """The fabricated study, pinned once and threaded through every prompt.

    This is what stops the Methods and Results sections from quietly disagreeing
    about how many people were in the study.
    """

    n: int
    population: str
    instrument: str
    key_stat: str
    effect_size: str


class Citation(BaseModel):
    key: str  # alphanumeric only; goes into \cite and \bibitem, see latex.ident
    short: str  # "Marchetti et al." -- the author-year label natbib renders
    authors: str  # display string, already in the order you want it printed
    title: str
    venue: str
    year: int
    volume: str | None = None
    pages: str | None = None
    doi: str | None = None

    @field_validator("key")
    @classmethod
    def _alnum(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]+", v):
            raise ValueError(f"citation key must be alphanumeric, got {v!r}")
        return v

    @field_validator("doi")
    @classmethod
    def _unresolvable(cls, v: str | None) -> str | None:
        """Force every DOI into the reserved 10.0000 prefix, which never resolves."""
        if v is None:
            return None
        suffix = v.split("/", 1)[1] if "/" in v else v
        return f"10.0000/{suffix}"


class PaperSpec(BaseModel):
    claim: str
    field: str
    title: str
    # Centre running header. arxiv.sty would otherwise use the full title, which
    # runs straight into the right-hand header.
    short_title: str | None = None
    authors: list[Author]
    date: str
    abstract: str
    keywords: list[str]
    dataset: Dataset
    sections: list[Section]
    citations: list[Citation]

    # The stamp down the left edge of page 1. arXiv adds this at ingest, so it is
    # the single highest-signal detail in the document -- and the best place to
    # make sure the thing can never be mistaken for real.
    arxiv_id: str = "2699.99999v1"
    arxiv_category: str = "cs.SAT"

    disclosure: Disclosure = "subtle"

    @field_validator("arxiv_id")
    @classmethod
    def _must_be_impossible(cls, v: str) -> str:
        """Reject any ID that could belong to a real paper.

        Real arXiv IDs are YYMM.NNNNN with a valid month, so anything with a
        month outside 01-12 is guaranteed never to resolve. Enforcing it here
        rather than by convention means no amount of prompt drift can produce a
        document that points at somebody's actual work.
        """
        m = _ARXIV_ID.match(v)
        if not m:
            raise ValueError(f"arxiv_id must look like YYMM.NNNNN[vN], got {v!r}")
        month = int(m.group(2))
        if 1 <= month <= 12:
            raise ValueError(
                f"arxiv_id {v!r} has a valid month ({month:02d}) and could collide with a "
                "real paper -- use an impossible month such as 99"
            )
        return v

    @model_validator(mode="after")
    def _citations_resolve(self) -> PaperSpec:
        """Every [[key]] in the prose must exist in the bibliography.

        A model that invents a key it never defines produces a silent bold `?`
        in the PDF, which is exactly the kind of tell that ruins the joke.
        """
        from .latex import cite_keys

        known = {c.key for c in self.citations}
        prose = [self.abstract] + [p for s in self.sections for p in s.paragraphs]
        used = {k for text in prose for k in cite_keys(text)}
        if unknown := sorted(used - known):
            raise ValueError(f"prose cites undefined keys: {unknown}")
        return self

    @model_validator(mode="after")
    def _default_short_title(self) -> PaperSpec:
        if not self.short_title:
            head = self.title.split(":")[0]
            self.short_title = head if len(head) <= 60 else head[:57].rstrip() + "..."
        return self

    @property
    def stamp(self) -> str:
        return f"arXiv:{self.arxiv_id}  [{self.arxiv_category}]  {self.date}"

    @property
    def slug(self) -> str:
        """Filename stem. Carries the disclosure, since filenames survive sharing."""
        head = re.sub(r"[^a-z0-9]+", "-", self.short_title.lower()).strip("-")
        return f"{head[:50].rstrip('-')}-SATIRE"

    @property
    def figures(self) -> list[Figure]:
        return [s.figure for s in self.sections if s.figure is not None]

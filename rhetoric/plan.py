"""Stage 2: one-line claim -> PaperPlan.

The plan is the outline, not the paper: section headings with *beats* rather
than prose, plus the things every later call has to agree about -- the pinned
fake dataset, the bibliography, the figures. Generating the whole document in
one shot gives mush, because the model cannot hold a consistent fabricated
methodology across two thousand words. Stage 3 turns beats into paragraphs.

Every field is required and the shapes are flat: a constrained decode follows a
schema much better when there is nothing optional to reason about.

    python -m rhetoric.plan --claim "Cereal is a soup" -o specs/cereal.plan.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, model_validator

from .llm import ModelConfig, ModelError, build_request, complete_json, list_models
from .schema import Citation, Dataset, FigureKind

PROMPTS = Path(__file__).resolve().parent / "prompts"

# Constrained as an enum rather than asked for in the prompt: a small model told
# to "use these headings" will happily invent a "References" section, and the
# grammar can simply make that unrepresentable.
SectionHeading = Literal[
    "Introduction", "Related Work", "Methods", "Results", "Discussion", "Limitations", "Conclusion"
]
CANONICAL_ORDER = list(SectionHeading.__args__)
REQUIRED_SECTIONS = {"Introduction", "Methods", "Results", "Conclusion"}

# Few-shot examples get copied, not imitated. qwen3:8b handed back a hot dog
# paper written by the cereal exemplar's authors, at the cereal exemplar's
# institutions, with its sample size, effect size and citation keys intact --
# despite being told in the prompt not to. Asking louder does not work; checking
# does. These are the distinctive tokens from prompts and reference material
# that must never appear in generated output.
EXEMPLAR_TOKENS = (
    "Okonkwo-Reyes", "Lindqvist", "Beaumont", "Whitmore", "Breakfast Dynamics",
    "vandermeer", "fitzgerald2023", "marchetti2019", "nakamura2022", "okonkwo2021",
    "Applied Gastronomy", "Culinary Ontology", "Food Physics Letters",
    "Empirical Foodways", "Comestible Philosophy",
    "1247", "1.84", "89.2", "gazpacho", "Gazpacho", "bisque", "Bisque",
    "Chilled Suspension", "CSPI", "soup-ness",
)

# What each figure kind reads off `categories` / `values` / `x_min` / `x_max`.
# synth.py expands these into observations.
FIGURE_SHAPE_DOC = {
    "bar_errorbar": "categories = bar labels, values = bar heights (one per category)",
    "box": "categories = box labels, values = median per box",
    "scatter_regression": "values = [y at x_min, y at x_max], x_min/x_max = x range",
    "line": "categories = series names, values = starting level per series",
}


class PlanAuthor(BaseModel):
    name: str
    affiliation: str
    email: str


class PlannedFigure(BaseModel):
    """A figure's shape. synth.expand fills in the observations."""

    kind: FigureKind
    label: str
    caption: str
    xlabel: str
    ylabel: str
    categories: list[str]
    values: list[float]
    spread: float
    x_min: float
    x_max: float

    @model_validator(mode="after")
    def _has_what_its_kind_needs(self) -> PlannedFigure:
        if not self.values:
            raise ValueError(f"figure {self.label!r} has no values")
        if self.kind in ("bar_errorbar", "box") and len(self.categories) != len(self.values):
            raise ValueError(
                f"figure {self.label!r} has {len(self.categories)} categories "
                f"but {len(self.values)} values"
            )
        if self.kind == "line" and not self.categories:
            raise ValueError(f"line figure {self.label!r} has no series names")
        return self


class PlanSection(BaseModel):
    heading: SectionHeading
    beats: list[str]
    figure: PlannedFigure | None


class PaperPlan(BaseModel):
    claim: str
    field: str
    title: str
    short_title: str
    authors: list[PlanAuthor]
    keywords: list[str]
    dataset: Dataset
    abstract_beats: list[str]
    sections: list[PlanSection]
    citations: list[Citation]

    @model_validator(mode="after")
    def _is_not_copied_from_the_exemplar(self) -> PaperPlan:
        """Reject wholesale reuse of the few-shot example.

        The prompt asks for original names and numbers; models comply with the
        shape and ignore the request. This makes the instruction enforceable.
        """
        blob = self.model_dump_json()
        if found := sorted({t for t in EXEMPLAR_TOKENS if t in blob}):
            raise ValueError(
                f"copied from the example: {found}. Invent your own authors, "
                "institutions, sample size, statistics and references."
            )
        return self

    @model_validator(mode="after")
    def _sections_are_coherent(self) -> PaperPlan:
        headings = [s.heading for s in self.sections]
        if len(headings) != len(set(headings)):
            raise ValueError(f"duplicate section headings: {headings}")
        if missing := REQUIRED_SECTIONS - set(headings):
            raise ValueError(f"missing required section(s): {sorted(missing)}")

        # Labels become PNG filenames and LaTeX \label keys, so a collision
        # silently overwrites one figure with another.
        labels = [s.figure.label for s in self.sections if s.figure]
        if len(labels) != len(set(labels)):
            raise ValueError(f"duplicate figure labels: {labels}. Each figure needs its own.")

        self.sections.sort(key=lambda s: CANONICAL_ORDER.index(s.heading))
        return self

    @model_validator(mode="after")
    def _citations_resolve(self) -> PaperPlan:
        """Beats may only cite keys the bibliography defines.

        Caught here rather than repaired, so the retry loop hands the model back
        its own sentence to fix instead of deleting words out of it.
        """
        from .latex import cite_keys

        known = {c.key for c in self.citations}
        beats = self.abstract_beats + [b for s in self.sections for b in s.beats]
        used = {k for beat in beats for k in cite_keys(beat)}
        if unknown := sorted(used - known):
            raise ValueError(
                f"beats cite undefined keys: {unknown}. Either define them in "
                "`citations` or cite a key that already exists."
            )
        return self


# ---------------------------------------------------------------- repair


def _alnum(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", key)


def _slug(label: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", label.lower())).strip("-") or "figure"


def repair(raw: dict, claim: str) -> tuple[dict, list[str]]:
    """Fix the *mechanical* mistakes in model output before validation.

    A constrained decode guarantees the shape of the JSON, never its content.
    Citation keys come back snake_cased and figure labels come back with spaces
    and capitals -- both unambiguous to fix, and both fatal to the LaTeX build.

    Semantic mistakes are deliberately not repaired here. A beat citing a key
    that was never defined used to be patched by deleting the marker, which left
    wreckage behind ("Follows from ." / "by [[a]],, the effect holds"). It is a
    content error, so it is left for validation to catch and the retry loop to
    fix, where the model repairs its own sentence.
    """
    notes: list[str] = []
    raw = json.loads(json.dumps(raw))
    raw["claim"] = claim

    # Citation keys: the schema demands alphanumeric, models emit snake_case.
    remap: dict[str, str] = {}
    for citation in raw.get("citations", []):
        old = citation.get("key", "")
        new = _alnum(old)
        if new and new != old:
            remap[old] = new
            citation["key"] = new
    if remap:
        notes.append(f"normalised {len(remap)} citation key(s)")

    def renumber(text: str) -> str:
        def sub(match: re.Match[str]) -> str:
            keys = [remap.get(k.strip(), _alnum(k.strip())) for k in match.group(1).split(",")]
            return "[[" + ",".join(keys) + "]]"

        return re.sub(r"\[\[([^\]]+)\]\]", sub, text)

    beat_lists = [raw.get("abstract_beats", [])]
    beat_lists += [s.get("beats", []) for s in raw.get("sections", [])]
    for beats in beat_lists:
        for i, beat in enumerate(beats):
            beats[i] = renumber(beat)

    for section in raw.get("sections", []):
        figure = section.get("figure")
        if figure and "label" in figure:
            slugged = _slug(figure["label"])
            if slugged != figure["label"]:
                figure["label"] = slugged
                notes.append(f"slugged figure label -> {slugged!r}")

    if not raw.get("short_title"):
        raw["short_title"] = raw.get("title", "").split(":")[0][:60]

    return raw, notes


# ---------------------------------------------------------------- generation


def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def build_prompts(claim: str) -> tuple[str, str]:
    shapes = "\n".join(f"  - {k}: {v}" for k, v in FIGURE_SHAPE_DOC.items())
    system = _prompt("plan.system.md").replace("{{FIGURE_SHAPES}}", shapes)
    user = _prompt("plan.user.md").replace("{{CLAIM}}", claim)
    return system, user


def _reasons(exc: ValidationError) -> str:
    """The `msg` lines only. Pydantic's full rendering embeds the entire input."""
    return "\n".join(f"- {e['msg'].removeprefix('Value error, ')}" for e in exc.errors())


def generate(claim: str, cfg: ModelConfig, attempts: int = 3) -> PaperPlan:
    """Generate a plan, retrying with the validation error fed back on failure."""
    system, base_user = build_prompts(claim)
    schema = PaperPlan.model_json_schema()
    user = base_user
    last: ValidationError | None = None

    for attempt in range(1, attempts + 1):
        raw = complete_json(cfg, system, user, schema, name="paper_plan")
        fixed, notes = repair(raw, claim)
        for note in notes:
            print(f"  repair: {note}", file=sys.stderr)
        try:
            return PaperPlan.model_validate(fixed)
        except ValidationError as exc:
            # Deliberately narrow: a blanket `except Exception` here would
            # swallow a bug in this file and burn every retry on it.
            last = exc
            reasons = _reasons(exc)
            print(f"  attempt {attempt}/{attempts} rejected:\n{reasons}", file=sys.stderr)
            # Rebuilt from the base each time, never appended to. Accumulating
            # feedback grows the prompt every retry, which on a local model was
            # enough to push the runtime into an out-of-memory crash by attempt
            # three -- and buries the current error under the stale ones.
            user = (
                f"{base_user}\n\nA previous attempt was rejected for these reasons:\n"
                f"{reasons}\n\nReturn corrected JSON. Fix exactly these problems."
            )

    raise ModelError(f"no valid plan after {attempts} attempts. Last error:\n{_reasons(last)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Claim -> paper plan (stage 2).")
    ap.add_argument("--claim", help='e.g. "Cereal is a soup"')
    ap.add_argument("--models", action="store_true", help="list reachable models, cheapest first")
    ap.add_argument("-o", "--out", type=Path, help="write plan JSON here (default: stdout)")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument(
        "--dry-run", action="store_true", help="print the request that would be sent, then exit"
    )
    args = ap.parse_args()

    cfg = ModelConfig.from_env()

    if args.models:
        for row in list_models(cfg):
            print(f"{row['out_per_m']:8.2f} $/M out  {row['context']:>9,} ctx  {row['id']}")
        return

    if not args.claim:
        ap.error("--claim is required (or pass --models)")

    if args.dry_run:
        system, user = build_prompts(args.claim)
        url, headers, body = build_request(
            cfg, system, user, PaperPlan.model_json_schema(), "paper_plan"
        )
        headers = {k: ("Bearer <redacted>" if k == "Authorization" else v) for k, v in headers.items()}
        print(json.dumps({"url": url, "headers": headers, "body": body}, indent=2))
        return

    print(f"planning with {cfg.provider}:{cfg.model}", file=sys.stderr)
    plan = generate(args.claim, cfg, attempts=args.attempts)
    out = plan.model_dump_json(indent=2)

    if args.out:
        args.out.write_text(out + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()

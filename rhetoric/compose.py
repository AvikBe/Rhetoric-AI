"""Plan -> PaperSpec.

The seam stage 3 plugs into. `compose` takes a plan plus the prose written for
it and assembles the spec the renderer consumes; everything that is not prose --
authors, dataset, citations, figures -- carries straight across.

Until stage 3 exists, `--stub` fills the prose with the beats themselves. The
output is a real PDF with a real bibliography and real figures, and placeholder
body text, which is enough to see the whole chain work:

    python -m rhetoric.plan --claim "Hot dogs are sandwiches" -o plan.json
    python -m rhetoric.compose --plan plan.json --stub -o specs/hotdog.json
    make paper SPEC=specs/hotdog.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .plan import PaperPlan
from .schema import Author, PaperSpec, Section
from .synth import expand

Prose = dict[str, list[str]]
"""Section heading -> paragraphs. `_abstract` holds the abstract's paragraphs."""

ABSTRACT = "_abstract"


def stub_prose(plan: PaperPlan) -> Prose:
    """Beats as placeholder prose, so the chain is runnable before stage 3."""
    prose: Prose = {ABSTRACT: [" ".join(plan.abstract_beats)]}
    for section in plan.sections:
        prose[section.heading] = list(section.beats)
    return prose


def compose(plan: PaperPlan, prose: Prose, disclosure: str = "subtle") -> PaperSpec:
    missing = [s.heading for s in plan.sections if s.heading not in prose]
    if missing or ABSTRACT not in prose:
        raise ValueError(f"no prose for: {missing + ([ABSTRACT] if ABSTRACT not in prose else [])}")

    return PaperSpec(
        claim=plan.claim,
        field=plan.field,
        title=plan.title,
        short_title=plan.short_title,
        authors=[Author(**a.model_dump()) for a in plan.authors],
        date=_today(),
        abstract=" ".join(prose[ABSTRACT]),
        keywords=plan.keywords,
        dataset=plan.dataset,
        sections=[
            Section(
                heading=s.heading,
                paragraphs=prose[s.heading],
                figure=expand(s.figure) if s.figure else None,
            )
            for s in plan.sections
        ],
        citations=plan.citations,
        disclosure=disclosure,
    )


def _today() -> str:
    from datetime import date

    d = date.today()
    return f"{d.day} {d:%b} {d.year}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Plan -> paper spec.")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--prose", type=Path, help="JSON: heading -> [paragraphs] (stage 3 output)")
    ap.add_argument("--stub", action="store_true", help="use the beats as placeholder prose")
    ap.add_argument("--disclosure", choices=("subtle", "loud"), default="subtle")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    if not (args.prose or args.stub):
        ap.error("pass --prose (stage 3 output) or --stub")

    plan = PaperPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    prose = (
        json.loads(args.prose.read_text(encoding="utf-8")) if args.prose else stub_prose(plan)
    )

    spec = compose(plan, prose, disclosure=args.disclosure)
    args.out.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

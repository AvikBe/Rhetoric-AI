"""Stage 3: PaperPlan -> prose.

One call per section plus one for the abstract, run concurrently. Not one call
for the whole paper: a model asked for two thousand words of fabricated
methodology loses track of its own study halfway through, and every section
after that quietly contradicts the ones before.

Each call is handed the pinned dataset and, where the section has one, the
figure's actual numbers -- so Results can quote the bars it is describing
instead of inventing a second set.

    python -m rhetoric.prose --plan plan.json -o prose.json
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, ValidationError, field_validator

from .compose import ABSTRACT, Prose
from .guards import assert_no_latex, assert_original
from .llm import ModelConfig, ModelError, complete_json
from .plan import PROMPTS, PaperPlan, PlanSection

# A paragraph shorter than this is a stub, not prose. Models under a grammar
# will happily satisfy `list[str]` with "Results were significant."
MIN_PARAGRAPH_CHARS = 220
MAX_PARAGRAPHS = 5


class SectionProse(BaseModel):
    paragraphs: list[str]

    @field_validator("paragraphs")
    @classmethod
    def _substantial(cls, v: list[str]) -> list[str]:
        if not 1 <= len(v) <= MAX_PARAGRAPHS:
            raise ValueError(f"expected 1-{MAX_PARAGRAPHS} paragraphs, got {len(v)}")
        for i, p in enumerate(v, 1):
            assert_no_latex(p, f"paragraph {i}")
            if len(p) < MIN_PARAGRAPH_CHARS:
                raise ValueError(
                    f"paragraph {i} is {len(p)} characters; write at least "
                    f"{MIN_PARAGRAPH_CHARS}. Each paragraph is a full academic "
                    "paragraph, not a sentence."
                )
        assert_original(*v)
        return v


def _figure_brief(section: PlanSection) -> str:
    """The figure's real numbers, so the prose describes the chart that exists."""
    f = section.figure
    if f is None:
        return "This section has no figure."
    pairs = ", ".join(f"{c}={v}" for c, v in zip(f.categories, f.values)) or str(f.values)
    return (
        f"This section carries Figure with caption: {f.caption}\n"
        f"Its data: {pairs}. Refer to it as a figure and quote these numbers "
        f"exactly; do not invent different ones."
    )


def build_prompts(plan: PaperPlan, heading: str, beats: list[str], brief: str) -> tuple[str, str]:
    system = (PROMPTS / "prose.system.md").read_text(encoding="utf-8")
    citations = "\n".join(
        f"  [[{c.key}]] = {c.authors} ({c.year}), {c.title}" for c in plan.citations
    )
    user = (
        (PROMPTS / "prose.user.md")
        .read_text(encoding="utf-8")
        .replace("{{CLAIM}}", plan.claim)
        .replace("{{FIELD}}", plan.field)
        .replace("{{TITLE}}", plan.title)
        .replace("{{DATASET}}", json.dumps(plan.dataset.model_dump(), indent=2))
        .replace("{{HEADING}}", heading)
        .replace("{{BEATS}}", "\n".join(f"  {i}. {b}" for i, b in enumerate(beats, 1)))
        .replace("{{FIGURE}}", brief)
        .replace("{{CITATIONS}}", citations or "  (none)")
    )
    return system, user


def _generate_one(
    plan: PaperPlan, heading: str, beats: list[str], brief: str, cfg: ModelConfig, attempts: int
) -> list[str]:
    from .plan import _reasons  # same concise-feedback rendering as stage 2

    system, base_user = build_prompts(plan, heading, beats, brief)
    schema = SectionProse.model_json_schema()
    user = base_user
    last: ValidationError | None = None

    for attempt in range(1, attempts + 1):
        raw = complete_json(cfg, system, user, schema, name="section_prose")
        try:
            return SectionProse.model_validate(raw).paragraphs
        except ValidationError as exc:
            last = exc
            reasons = _reasons(exc)
            print(f"  {heading}: attempt {attempt}/{attempts} rejected:\n{reasons}", file=sys.stderr)
            # Rebuilt from the base, never appended to -- see plan.generate.
            user = (
                f"{base_user}\n\nA previous attempt was rejected for these reasons:\n"
                f"{reasons}\n\nReturn corrected JSON. Fix exactly these problems."
            )

    raise ModelError(f"{heading}: no valid prose after {attempts} attempts:\n{_reasons(last)}")


def generate(plan: PaperPlan, cfg: ModelConfig, attempts: int = 3, workers: int = 4) -> Prose:
    """Every section plus the abstract, concurrently.

    The calls are independent -- each gets the same pinned dataset and its own
    beats -- so wall-clock time is roughly one section rather than seven.
    """
    jobs: list[tuple[str, list[str], str]] = [
        (ABSTRACT, plan.abstract_beats, "The abstract has no figure. Write exactly one paragraph.")
    ]
    jobs += [(s.heading, s.beats, _figure_brief(s)) for s in plan.sections]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_generate_one, plan, heading, beats, brief, cfg, attempts): heading
            for heading, beats, brief in jobs
        }
        return {futures[f]: f.result() for f in futures}


def main() -> None:
    ap = argparse.ArgumentParser(description="Plan -> prose (stage 3).")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = ModelConfig.from_env()
    plan = PaperPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))

    print(f"writing {len(plan.sections) + 1} sections with {cfg.provider}:{cfg.model}", file=sys.stderr)
    prose = generate(plan, cfg, attempts=args.attempts, workers=args.workers)

    args.out.write_text(json.dumps(prose, indent=2) + "\n", encoding="utf-8")
    words = sum(len(p.split()) for ps in prose.values() for p in ps)
    print(f"wrote {args.out} ({words} words)", file=sys.stderr)


if __name__ == "__main__":
    main()

"""The whole pipeline as one call.

Until now the stages only composed as four CLI invocations glued together in the
Makefile, which the API cannot reuse. This is the seam both go through, so the
served path and the command-line path cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import plan as plan_stage
from . import prose as prose_stage
from .build import compile_pdf
from .compose import compose
from .llm import ModelConfig
from .plan import PaperPlan
from .render import render

Progress = Callable[[str], None]
"""Called with a one-line status as each stage starts. Used by the job store."""

MAX_CLAIM_CHARS = 300


@dataclass(frozen=True)
class Paper:
    plan: PaperPlan
    pdf: Path
    words: int

    @property
    def title(self) -> str:
        return self.plan.title


def write(
    claim: str,
    outdir: Path,
    cfg: ModelConfig,
    *,
    disclosure: str = "subtle",
    attempts: int = 3,
    workers: int = 4,
    on_progress: Progress | None = None,
) -> Paper:
    """Claim in, PDF out. Writes plan.json and prose.json alongside it.

    The intermediates are kept deliberately: a plan is worth reading, and worth
    editing by hand before spending tokens on prose.
    """
    claim = claim.strip()
    if not claim:
        raise ValueError("claim is empty")
    if len(claim) > MAX_CLAIM_CHARS:
        raise ValueError(f"claim is {len(claim)} characters; keep it under {MAX_CLAIM_CHARS}")

    say = on_progress or (lambda _: None)
    outdir.mkdir(parents=True, exist_ok=True)

    say("planning")
    paper_plan = plan_stage.generate(claim, cfg, attempts=attempts)
    (outdir / "plan.json").write_text(paper_plan.model_dump_json(indent=2), encoding="utf-8")

    say(f"writing {len(paper_plan.sections) + 1} sections")
    prose = prose_stage.generate(paper_plan, cfg, attempts=attempts, workers=workers)

    say("rendering")
    spec = compose(paper_plan, prose, disclosure=disclosure)
    (outdir / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")

    tex = render(spec, outdir)
    pdf = compile_pdf(tex)
    final = pdf.with_name(f"{spec.slug}.pdf")
    pdf.replace(final)

    words = sum(len(p.split()) for ps in prose.values() for p in ps)
    say("done")
    return Paper(plan=paper_plan, pdf=final, words=words)

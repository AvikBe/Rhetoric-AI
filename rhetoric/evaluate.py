"""Run the same claims through several models and compare.

Deadpan register is where models differ, and it is not predictable from size or
price, so the only way to choose one is to read the output. This runs the
matrix and writes a report whose point is the prose: the numbers say which
models work at all, and the Methods sections say which one is funny.

    python -m rhetoric.evaluate --models a,b,c --out evals/run1

Costs real money -- one paper per cell. Start with two claims and three models.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .llm import ModelConfig
from .pipeline import write

DEFAULT_CLAIMS = (
    "A hot dog is a sandwich",
    "Predictability is a sign of stupidity",
    "Being on time is a form of arrogance",
)

# The stages print rejections and repairs to stderr; capturing it is cheaper
# than threading a stats object through every call site for a dev tool.
_REJECTED = re.compile(r"rejected:")
_REPAIR = re.compile(r"^\s*repair: ", re.MULTILINE)


@dataclass
class Result:
    model: str
    claim: str
    ok: bool
    seconds: float = 0.0
    words: int = 0
    title: str = ""
    retries: int = 0
    repairs: int = 0
    error: str = ""
    rejections: list[str] = field(default_factory=list)
    pdf: str = ""


def _run_one(model: str, claim: str, outdir: Path, attempts: int) -> Result:
    import os

    os.environ["RHETORIC_MODEL"] = model
    log = io.StringIO()
    started = time.time()

    try:
        with contextlib.redirect_stderr(log):
            paper = write(claim, outdir, ModelConfig.from_env(), attempts=attempts)
        captured = log.getvalue()
        return Result(
            model=model,
            claim=claim,
            ok=True,
            seconds=round(time.time() - started, 1),
            words=paper.words,
            title=paper.title,
            retries=len(_REJECTED.findall(captured)),
            repairs=len(_REPAIR.findall(captured)),
            rejections=_rejection_reasons(captured),
            pdf=paper.pdf.name,
        )
    except Exception as exc:  # noqa: BLE001 -- a failed cell is a result, not a crash
        captured = log.getvalue()
        return Result(
            model=model,
            claim=claim,
            ok=False,
            seconds=round(time.time() - started, 1),
            retries=len(_REJECTED.findall(captured)),
            repairs=len(_REPAIR.findall(captured)),
            rejections=_rejection_reasons(captured),
            error=str(exc)[:300],
        )


def _rejection_reasons(captured: str) -> list[str]:
    """The `- reason` lines, deduplicated. Says *why* a model struggled."""
    return sorted({m.strip() for m in re.findall(r"^- (.+)$", captured, re.MULTILINE)})


def _methods(outdir: Path) -> str:
    """The Methods section, which is where register differences show up most."""
    spec_path = outdir / "spec.json"
    if not spec_path.exists():
        return ""
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for section in spec.get("sections", []):
        if section["heading"] == "Methods":
            return "\n\n".join(section["paragraphs"])
    return ""


def report(results: list[Result], outdir: Path) -> str:
    lines = ["# Model comparison", "", "| model | claim | ok | secs | words | retries | repairs |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        claim = r.claim if len(r.claim) <= 34 else r.claim[:31] + "..."
        lines.append(
            f"| `{r.model}` | {claim} | {'yes' if r.ok else 'NO'} | {r.seconds} | "
            f"{r.words} | {r.retries} | {r.repairs} |"
        )

    by_model: dict[str, list[Result]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)

    lines += ["", "## Per model", ""]
    for model, rows in by_model.items():
        done = [r for r in rows if r.ok]
        lines.append(f"**`{model}`** — {len(done)}/{len(rows)} completed")
        if done:
            lines.append(
                f"- median {sorted(r.seconds for r in done)[len(done) // 2]}s, "
                f"{sum(r.words for r in done) // len(done)} words avg, "
                f"{sum(r.retries for r in rows)} retries total"
            )
        for r in rows:
            if not r.ok:
                lines.append(f"- FAILED on {r.claim!r}: {r.error}")
            for reason in r.rejections:
                lines.append(f"  - rejected: {reason}")
        lines.append("")

    lines += ["## Methods sections", "",
              "_The numbers above say which models work. These say which one is funny._", ""]
    for r in results:
        if not r.ok:
            continue
        methods = _methods(outdir / _slug(r.model, r.claim))
        if methods:
            lines += [f"### `{r.model}` — {r.claim}", "", f"> {r.title}", "", methods, ""]
    return "\n".join(lines)


def _slug(model: str, claim: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{model} {claim}".lower()).strip("-")[:80]


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare models on the same claims.")
    ap.add_argument("--models", required=True, help="comma-separated model ids")
    ap.add_argument("--claims", nargs="*", default=list(DEFAULT_CLAIMS))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--attempts", type=int, default=3)
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    args.out.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []

    for model in models:
        for claim in args.claims:
            print(f"-> {model} :: {claim}", flush=True)
            result = _run_one(model, claim, args.out / _slug(model, claim), args.attempts)
            status = f"{result.seconds}s, {result.words}w, {result.retries} retries"
            print(f"   {'ok' if result.ok else 'FAILED'} ({status})", flush=True)
            if not result.ok:
                print(f"   {result.error}", flush=True)
            results.append(result)

    (args.out / "results.json").write_text(
        json.dumps([dataclasses.asdict(r) for r in results], indent=2), encoding="utf-8"
    )
    (args.out / "report.md").write_text(report(results, args.out), encoding="utf-8")
    print(f"\nwrote {args.out / 'report.md'}")


if __name__ == "__main__":
    main()

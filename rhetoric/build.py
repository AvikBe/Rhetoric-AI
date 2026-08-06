"""CLI: spec JSON in, PDF out.

    python -m rhetoric.build --spec specs/cereal_soup.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .render import render
from .schema import PaperSpec

NO_ENGINE = """\
No LaTeX engine found.

Install tectonic -- it is a single self-contained binary that downloads only the
packages this document needs, so you do not need a full TeX Live install:

    brew install tectonic

Alternatively install MacTeX/TeX Live for latexmk, or re-run with --no-pdf to
emit paper.tex only.
"""


def compile_pdf(tex: Path) -> Path:
    if shutil.which("tectonic"):
        cmd = ["tectonic", "--keep-logs", tex.name]
    elif shutil.which("latexmk"):
        cmd = ["latexmk", "-pdf", "-interaction=nonstopmode", tex.name]
    else:
        sys.exit(NO_ENGINE)

    # check=False: the return code is inspected below to surface the log path.
    proc = subprocess.run(cmd, cwd=tex.parent, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        sys.exit(f"\n{cmd[0]} failed. See {tex.with_suffix('.log')}")
    return tex.with_suffix(".pdf")


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a satirical arXiv-style preprint.")
    ap.add_argument("--spec", type=Path, required=True, help="paper spec JSON")
    ap.add_argument("--outdir", type=Path, default=Path("build"))
    ap.add_argument("--no-pdf", action="store_true", help="emit paper.tex and figures only")
    args = ap.parse_args()

    spec = PaperSpec.model_validate(json.loads(args.spec.read_text(encoding="utf-8")))
    tex = render(spec, args.outdir)
    print(f"wrote {tex}")

    if not args.no_pdf:
        pdf = compile_pdf(tex)
        # Rename late: tectonic derives the PDF name from the .tex, but the
        # filename is itself a disclosure surface once the file is shared.
        final = pdf.with_name(f"{spec.slug}.pdf")
        pdf.replace(final)
        print(f"wrote {final}")


if __name__ == "__main__":
    main()

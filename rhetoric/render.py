"""Spec -> LaTeX source tree.

Nothing here talks to a model. Given a PaperSpec this is fully deterministic,
which is what lets you iterate on the look of the PDF without burning tokens.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import figures
from .latex import body, finalize, ident, pdf_string
from .schema import PaperSpec

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
ASSETS = ROOT / "assets"


def make_env() -> Environment:
    """Jinja with LaTeX-safe delimiters.

    The defaults ({{ }}, {% %}) are unusable in a .tex file, so blocks become
    \\BLOCK{...}, variables \\VAR{...} and comments \\#{...}. `finalize` escapes
    every interpolated string, so a template author cannot forget to.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        finalize=finalize,
        autoescape=False,
    )
    env.filters["body"] = body
    env.filters["pdf"] = pdf_string
    env.filters["ident"] = ident
    return env


def render(spec: PaperSpec, outdir: Path) -> Path:
    """Write paper.tex, arxiv.sty and every figure PNG into `outdir`."""
    outdir.mkdir(parents=True, exist_ok=True)

    # arxiv.sty has to sit next to the .tex for \usepackage{arxiv} to find it.
    shutil.copy(ASSETS / "arxiv.sty", outdir / "arxiv.sty")

    figures.render_all(spec.figures, outdir)

    tex = outdir / "paper.tex"
    tex.write_text(make_env().get_template("paper.tex.j2").render(spec=spec), encoding="utf-8")
    return tex

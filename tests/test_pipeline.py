"""Stage 3 and the path from a plan to rendered LaTeX.

No model and no LaTeX engine required: `complete_json` is stubbed, and the
render assertions stop at paper.tex. The tectonic build is covered separately
and skipped when the binary is absent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from rhetoric import prose as prose_mod
from rhetoric.compose import ABSTRACT, compose, stub_prose
from rhetoric.llm import ModelConfig, strictify
from rhetoric.plan import PaperPlan
from rhetoric.prose import SectionProse, build_prompts
from rhetoric.render import ROOT, render

PARAGRAPH = "A sufficiently long academic paragraph. " * 8


def _cfg() -> ModelConfig:
    return ModelConfig(
        provider="openrouter", model="fake", temperature=0.9, max_tokens=100,
        num_ctx=8192, timeout=60, think=False, base_url="http://x", api_key="k",
    )


class TestProseValidation:
    def test_stub_paragraphs_are_rejected(self) -> None:
        """Under a grammar a model will satisfy list[str] with one sentence."""
        with pytest.raises(ValidationError, match="write at least"):
            SectionProse(paragraphs=["Results were significant."])

    def test_latex_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="contains LaTeX"):
            SectionProse(paragraphs=[PARAGRAPH + r"Use \textbf{bold}."])

    def test_exemplar_leakage_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="copied from the example"):
            SectionProse(paragraphs=["The gazpacho comparison holds. " * 10])

    def test_paragraph_cap(self) -> None:
        with pytest.raises(ValidationError, match="1-5 paragraphs"):
            SectionProse(paragraphs=[PARAGRAPH] * 6)


class TestProsePrompts:
    def test_figure_numbers_are_threaded_through(self, plan: PaperPlan) -> None:
        """Results must describe the chart that exists, not invent a second one."""
        results = next(s for s in plan.sections if s.heading == "Results")
        _, user = build_prompts(plan, results.heading, results.beats,
                                prose_mod._figure_brief(results))
        assert "Sub=8.1" in user and "Hot dog=7.6" in user
        assert "do not invent different ones" in user

    def test_pinned_dataset_and_citations_are_present(self, plan: PaperPlan) -> None:
        system, user = build_prompts(plan, "Methods", ["Define containment."], "no figure")
        assert "812" in user and "Structural Containment Index" in user
        assert "[[adeyemi2022]]" in user
        assert "{{" not in system + user


class TestFanOut:
    def test_every_section_is_routed_and_retried(self, plan: PaperPlan, monkeypatch) -> None:
        calls: list[str] = []
        lock = threading.Lock()
        live, peak = [0], [0]

        def fake(cfg, system, user, schema, name="response"):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            heading = m.group(1).strip() if (m := re.search(r"HEADING: (.+)", user)) else ABSTRACT
            time.sleep(0.15)
            with lock:
                calls.append(heading)
                live[0] -= 1
            # First Results attempt returns a stub, exercising the retry path.
            if heading == "Results" and calls.count("Results") == 1:
                return {"paragraphs": ["Too short."]}
            return {"paragraphs": [PARAGRAPH]}

        monkeypatch.setattr("rhetoric.prose.complete_json", fake)
        result = prose_mod.generate(plan, _cfg(), attempts=2, workers=4)

        assert set(result) == {ABSTRACT} | {s.heading for s in plan.sections}
        assert calls.count("Results") == 2, "invalid prose should be retried"
        assert peak[0] > 1, "sections should be generated concurrently"


class TestComposeAndRender:
    def test_stub_prose_renders(self, plan: PaperPlan, tmp_path: Path) -> None:
        """`compose --stub` gives a real PDF without stage 3."""
        spec = compose(plan, stub_prose(plan))
        tex = render(spec, tmp_path).read_text()
        assert r"\begin{document}" in tex and r"\end{document}" in tex

    def test_missing_prose_is_an_error(self, plan: PaperPlan) -> None:
        with pytest.raises(ValueError, match="no prose for"):
            compose(plan, {ABSTRACT: ["only the abstract"]})

    def test_rendered_tex_is_escaped_and_cited(self, plan: PaperPlan, tmp_path: Path) -> None:
        prose = stub_prose(plan)
        prose["Methods"] = ["Coverage was 50% of trials, per [[adeyemi2022]]."]
        tex = render(compose(plan, prose), tmp_path).read_text()

        assert r"50\% of trials" in tex
        assert r"\citep{adeyemi2022}" in tex

    def test_renderer_refuses_an_unsafe_identifier(self, plan: PaperPlan, tmp_path: Path) -> None:
        r"""The template must route labels through `ident`, not the escaper.

        The schema already restricts labels, so this bypasses it to prove the
        second line of defence: a label that would become `fig:a\_b` inside
        \csname has to raise here rather than produce a PDF build that dies
        with "Missing \endcsname inserted" pointing at the .aux file.
        """
        spec = compose(plan, stub_prose(plan))
        results = next(s for s in spec.sections if s.heading == "Results")
        object.__setattr__(results.figure, "label", "sci_by_form")

        with pytest.raises(ValueError, match="unsafe identifier"):
            render(spec, tmp_path)

    def test_every_label_interpolation_is_guarded(self) -> None:
        """Each `ident` site needs its own assertion.

        The behavioural test above only proves *some* guard fires: `ident`'s
        charset and LaTeX's special characters do not overlap, so no label can
        distinguish an unguarded \\label from an unguarded \\includegraphics.
        Dropping the filter from one site alone would go unnoticed.
        """
        template = (ROOT / "templates" / "paper.tex.j2").read_text()
        sites = re.findall(r"\\VAR\{s\.figure\.label[^}]*\}", template)

        assert len(sites) == 2, f"expected \\label and \\includegraphics, found {sites}"
        assert all("|ident" in site for site in sites), f"unguarded label: {sites}"

    def test_disclosure_layer_is_present(self, plan: PaperPlan, tmp_path: Path) -> None:
        tex = render(compose(plan, stub_prose(plan)), tmp_path).read_text()
        assert "2699.99999v1" in tex, "impossible arXiv id"
        assert "Satirical Preprint" in tex, "running header"
        assert "This paper is satire" in tex, "title footnote"
        assert r"\section*{Disclaimer}" in tex

    def test_loud_mode_adds_the_watermark(self, plan: PaperPlan, tmp_path: Path) -> None:
        subtle = render(compose(plan, stub_prose(plan), "subtle"), tmp_path).read_text()
        loud = render(compose(plan, stub_prose(plan), "loud"), tmp_path).read_text()
        assert r"\SetWatermarkText{SATIRE}" in loud
        assert r"\SetWatermarkText{SATIRE}" not in subtle

    def test_figures_are_written_next_to_the_tex(self, plan: PaperPlan, tmp_path: Path) -> None:
        render(compose(plan, stub_prose(plan)), tmp_path)
        assert (tmp_path / "sci-by-form.png").exists()
        assert (tmp_path / "arxiv.sty").exists()


class TestStrictSchema:
    def test_every_object_is_closed_and_required(self) -> None:
        """Strict structured-output APIs reject anything less."""
        schema = strictify(PaperPlan.model_json_schema())
        objects = [schema, *schema.get("$defs", {}).values()]
        for obj in (o for o in objects if o.get("type") == "object"):
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
class TestPdfBuild:
    def test_reference_spec_compiles(self, tmp_path: Path) -> None:
        out = tmp_path / "build"
        # sys.executable, not a hardcoded .venv path -- CI does not have one.
        proc = subprocess.run(
            [sys.executable, "-m", "rhetoric.build",
             "--spec", str(ROOT / "specs" / "cereal_soup.json"), "--outdir", str(out)],
            capture_output=True, text=True, check=False, cwd=ROOT,
        )
        # Surface the build log; a bare CalledProcessError says nothing useful
        # about which LaTeX line failed.
        assert proc.returncode == 0, f"build failed:\n{proc.stdout}\n{proc.stderr}"
        pdfs = list(out.glob("*-SATIRE.pdf"))
        assert len(pdfs) == 1 and pdfs[0].stat().st_size > 20_000

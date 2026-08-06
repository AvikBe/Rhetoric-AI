# Rhetoric-AI

Generates satirical arXiv-style preprints to jokingly win arguments. Every
output is marked as fiction in four independent ways (see [Disclaimer
layer](#disclaimer-layer)).

**Status: stages 1–2 of 4.** The renderer is complete and verified, and a claim
now generates a plan via schema-constrained decoding. Stage 3 (prose) is not
built, so body text is currently the plan's beats used as placeholders — see
[Pipeline](#pipeline).

## Quick start

Requires Python 3.11+ and [tectonic](https://tectonic-typesetting.github.io/)
(`brew install tectonic` — a single self-contained binary that fetches only the
LaTeX packages this document needs, so no TeX Live install).

```bash
make venv
```

```bash
make paper && open build/paper.pdf
```

`make venv` needs [uv](https://docs.astral.sh/uv/). The Makefile runs
`.venv/bin/python` rather than an ambient `python3`, which on macOS is often the
system 3.9 without the dependencies installed.

Render a different spec, or stop at LaTeX if you have no engine installed:

```bash
make paper SPEC=specs/your_claim.json
```

```bash
make tex
```

## Pipeline

```
claim ──▶ PaperPlan ──▶ [stage 3: prose] ──▶ PaperSpec ──▶ paper.tex ──▶ PDF
          (stage 2)                          (compose)      (Jinja2)    (tectonic)
             │                                   │
        constrained                         synth ──▶ matplotlib ──▶ *.png
          decode
```

Stage 3 does not exist yet, so `compose --stub` uses the plan's beats as
placeholder body text. Everything else on the path is real:

```bash
.venv/bin/python -m rhetoric.plan --claim "A hot dog is a sandwich" -o plan.json
```

```bash
.venv/bin/python -m rhetoric.compose --plan plan.json --stub -o specs/hotdog.json
```

```bash
make paper SPEC=specs/hotdog.json
```

| File | Role |
|---|---|
| [llm.py](rhetoric/llm.py) | Schema-constrained decoding against Ollama or any OpenAI-shaped endpoint. |
| [plan.py](rhetoric/plan.py) | Stage 2. `PaperPlan`, the repair pass, and the retry loop. |
| [prompts/](rhetoric/prompts) | The register lives here. Iterate without touching code. |
| [compose.py](rhetoric/compose.py) | Plan + prose → `PaperSpec`. The seam stage 3 plugs into. |
| [synth.py](rhetoric/synth.py) | Figure shape → observations. |
| [schema.py](rhetoric/schema.py) | The contract between model and renderer. Every validator is a failure mode a small model actually hits. |
| [latex.py](rhetoric/latex.py) | The only module allowed to emit backslashes. |
| [figures.py](rhetoric/figures.py) | Four fixed chart templates. The model fills in numbers, never code. |
| [render.py](rhetoric/render.py) | Jinja environment with LaTeX-safe delimiters. |
| [templates/paper.tex.j2](templates/paper.tex.j2) | The document, built on the `arxiv.sty` preprint style. |
| [specs/cereal_soup.json](specs/cereal_soup.json) | Reference output, and the few-shot exemplar for the prompts. |

### Model configuration

```bash
export RHETORIC_PROVIDER=ollama      # or: openrouter
export RHETORIC_MODEL=qwen3:8b       # provider-specific id
export RHETORIC_MAX_TOKENS=6000      # a plan needs ~5k
export RHETORIC_NUM_CTX=8192         # ollama; oversizing this is not free
export RHETORIC_TIMEOUT=2400         # local models are slow
```

`--dry-run` prints the exact request without sending it.

Three things measured on qwen3:8b, an M2 with 17 GB, that will bite on any
comparable setup:

- **Reasoning models must have thinking disabled.** The grammar constrains the
  content channel, not the reasoning one, so qwen3 spent 200 of 200 tokens on a
  990-character think block and returned *empty content*. `think: false` is now
  sent by default, with a fallback for models that have no reasoning channel.
- **Context size is not free.** The KV cache is allocated up front; an
  oversized `num_ctx` is the difference between fitting on the GPU and crawling.
- **It is slow.** ~3 tok/s for an 8B on this machine, so a plan takes roughly
  10 minutes. Hosted inference is the answer if that matters — the model is a
  config string.

### What the schema enforces vs. what the prompt asks for

Anything the grammar can make unrepresentable belongs in the schema, not the
prompt. Section headings are a `Literal` enum, because a small model told to
"use these headings" will invent a *References* section; as an enum it simply
cannot. The same applies to figure kinds.

What is left over splits in two, and the split matters:

- **Mechanical** mistakes are repaired silently — snake_cased citation keys,
  figure labels with spaces and capitals. Unambiguous to fix, fatal to the
  LaTeX build if left.
- **Semantic** mistakes are *not* repaired. A beat citing a key that was never
  defined used to be patched by deleting the marker, which left wreckage
  behind (`"Follows from ."`, `"by [[a]],, the effect holds"`). It is now a
  validation error, and the retry loop hands the model back its own sentence
  to fix.

Retries feed the validation error into the next attempt, so the model corrects
exactly what failed.

### Few-shot examples get copied, not imitated

Given a complete worked example, qwen3:8b returned a hot dog paper written by
the cereal exemplar's authors, at the cereal exemplar's institutions, with its
sample size, effect size and citation keys intact — while the prompt was
explicitly telling it not to reuse any of them.

Asking louder does not fix this. Two things did:

1. The few-shot in [plan.user.md](rhetoric/prompts/plan.user.md) is now
   *fragments from unrelated papers* rather than one worked example. There is a
   tone to match and no paper to clone.
2. `EXEMPLAR_TOKENS` in [plan.py](rhetoric/plan.py) makes the instruction
   enforceable. Distinctive names, journals and numbers from the prompts are
   rejected in output, and the retry loop regenerates.

If you edit the prompts, add any new distinctive proper nouns to that list.

### Two invariants worth preserving

**The model never emits LaTeX.** It emits plain prose; `latex.py` adds every
backslash. Jinja's `finalize` hook escapes *every* interpolated value, so
escaping is opt-out rather than opt-in — a template author cannot forget it.
Small models produce unbalanced braces and stray `$` often enough to break a
pipeline otherwise.

**Identifiers bypass escaping, and are charset-restricted instead.** Labels,
citation keys and graphics filenames end up inside `\csname`, where an escaped
underscore becomes a control sequence and the build dies with a baffling
`Missing \endcsname inserted`. So figure labels are `[a-z0-9-]+` and citation
keys are `[A-Za-z0-9]+`, enforced in the schema, and `latex.ident` re-asserts
that before letting anything through unescaped.

### Citations

Prose carries `[[key]]` markers, which become `\citep{key}` after escaping. This
keeps the model on plain text while still letting the body cite the
bibliography. A marker referencing a key that isn't in `citations` fails
validation rather than rendering as a silent bold `?`.

## Disclosure layer

The document is *meant* to look real, so a line of small print on page 1 is not
enough — it disappears the moment someone screenshots the abstract. But a
watermark across every page makes the thing unshareable, which defeats the
joke.

So the disclosure lives where a real preprint already keeps its status notes.
It reads as formatting rather than as a stamp, and it's funnier, because it
stays in character.

`disclosure: "subtle"` (the default):

1. **Running header** on every page: `Satirical Preprint`. This is the
   crop-resistant one — a screenshot of any page carries it.
2. **Title footnote** (the dagger), exactly where real preprints put "Under
   review, do not distribute". States plainly what the document is.
3. **Subtitle** under the title rule: `A Satirical Preprint`.
4. **Front-matter statements** before the references — Data availability,
   Funding, Competing interests — in the slot real papers use.
5. **Footer band** on every page, small, in red.
6. **Filename**: `<slug>-SATIRE.pdf`, which survives being sent as a file.

`disclosure: "loud"` adds the diagonal `SATIRE` watermark and a boxed banner
above the title. Use it if the paper is going somewhere you don't control.

Plus three things that fail closed if someone tries to check the paper:

- The **arXiv stamp** down the left edge of page 1 uses an impossible month
  (`2699.99999v1`). `PaperSpec` *rejects* any ID with a valid month, so no
  amount of prompt drift can produce an identifier that points at a real
  submission.
- Every **DOI** is rewritten into the reserved `10.0000/` prefix, which does not
  resolve, and is labelled as fabricated in the reference list.
- A **Disclaimer section** names the claim, the non-existent sample and the
  invented statistic explicitly.

## Roadmap

Stages 1 and 2 are done. Remaining:

3. **Section generation** — fan out one call per section, each receiving the
   pinned `dataset` and that section's beats, writing to `Prose` in
   [compose.py](rhetoric/compose.py). Include 2–3 exemplar paragraphs from
   `specs/cereal_soup.json` in the prompt: small models drift into telling
   jokes, and the humour depends entirely on nobody breaking character.
4. **Serve it** — FastAPI + Redis/arq, since a compile takes several seconds.

Model choice: route through OpenRouter so a flash-tier open model is a config
string, and pick on output quality rather than price — at ~20k output tokens a
paper, cost is a rounding error either way. Run ~10 fixed claims through each
candidate and read the Methods sections; deadpan register is where models
actually differ.

## Notes

- Figures float, so they may land a page after the text that references them.
  Prose says "Figure 1" literally rather than using `\ref`, which keeps the
  model from having to produce cross-references.
- `assets/arxiv.sty` is the widely-used
  [kourgeorge/arxiv-style](https://github.com/kourgeorge/arxiv-style) preprint
  template. arXiv itself has no house style; this is what the look comes from.

# Rhetoric-AI

Generates satirical arXiv-style preprints to jokingly win arguments. Every
output is marked as fiction in four independent ways (see [Disclaimer
layer](#disclaimer-layer)).

**Status: complete.** Claim in, PDF out, on the command line or over HTTP. A
full paper is a plan call plus seven parallel prose calls — roughly 90 seconds
and a fraction of a cent.

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

## Tests

```bash
make test
```

141 tests, no API key or model required — `complete_json` is stubbed and the
render assertions stop at `paper.tex`. The tectonic build is covered too, and
skipped when the binary is absent.

Every test corresponds to a bug that actually reached a broken PDF or a failed
build; there is no coverage-padding here. The suite is mutation-checked: each
fix is reverted in turn and the run must fail. That found two tests that passed
against broken code — one asserted on a string the code never emits, and one
used a fixture label that escaping does not change, so it could not tell a
guarded interpolation from an unguarded one.

## Pipeline

```
claim ──▶ PaperPlan ──▶ prose ──▶ PaperSpec ──▶ paper.tex ──▶ PDF
          (stage 2)    (stage 3)  (compose)     (Jinja2)    (tectonic)
             │             │          │
         constrained   fanned out   synth ──▶ matplotlib ──▶ *.png
           decode      per section
```

The whole thing:

```bash
make write CLAIM="A hot dog is a sandwich"
```

Or serve it:

```bash
make serve
```

```bash
curl -X POST localhost:8000/papers -H 'content-type: application/json' -d '{"claim": "A hot dog is a sandwich"}'
```

A paper takes about ninety seconds, so `POST /papers` returns a job id and the
work happens in the background. Poll `GET /papers/{id}` and fetch
`GET /papers/{id}/pdf` when it is `done`. Interactive docs at `/docs`.

**The API cannot turn the disclosure layer off.** `disclosure` chooses how loud
the markings are, never whether they exist. Serving this makes it easy to hand
someone a paper that looks real, and the markings are what keep that a joke
rather than a forgery.

Or one stage at a time — `plan.json` is worth reading and editing by hand
before spending tokens on prose:

```bash
.venv/bin/python -m rhetoric.plan  --claim "A hot dog is a sandwich" -o plan.json
```

```bash
.venv/bin/python -m rhetoric.prose --plan plan.json -o prose.json
```

```bash
.venv/bin/python -m rhetoric.compose --plan plan.json --prose prose.json -o specs/hotdog.json
```

`compose --stub` substitutes the plan's beats for prose, which renders a real
PDF without stage 3 — useful for working on the template.

| File | Role |
|---|---|
| [app.py](rhetoric/app.py) | Stage 4. The HTTP surface. |
| [jobs.py](rhetoric/jobs.py) | Filesystem job store, one directory per job. |
| [pipeline.py](rhetoric/pipeline.py) | The whole run as one call. CLI and API share it. |
| [llm.py](rhetoric/llm.py) | Schema-constrained decoding against Ollama or any OpenAI-shaped endpoint. |
| [plan.py](rhetoric/plan.py) | Stage 2. `PaperPlan`, the repair pass, and the retry loop. |
| [prose.py](rhetoric/prose.py) | Stage 3. One call per section, fanned out concurrently. |
| [guards.py](rhetoric/guards.py) | Checks that make prompt instructions enforceable. |
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

Put the key in `.env` at the repo root — gitignored, loaded automatically,
never logged:

```bash
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
```

```bash
.venv/bin/python -m rhetoric.plan --models    # reachable models, cheapest first
```

Defaults to OpenRouter. Everything else is optional:

```bash
export RHETORIC_MODEL=...             # provider-specific id
export RHETORIC_MAX_TOKENS=6000       # a plan needs ~5k
export RHETORIC_TIMEOUT=600
export RHETORIC_PROVIDER=ollama       # local; then RHETORIC_NUM_CTX applies
```

`--dry-run` prints the exact request without sending it.

#### If you do run locally

Three things measured on qwen3:8b on an M2 with 17 GB, all of which will bite
on any comparable setup:

- **Reasoning models must have thinking disabled.** The grammar constrains the
  content channel, not the reasoning one, so qwen3 spent 200 of 200 tokens on a
  990-character think block and returned *empty content*. `think: false` is now
  sent by default, with a fallback for models that have no reasoning channel.
- **Context size is not free.** The KV cache is allocated up front; an
  oversized `num_ctx` is the difference between fitting on the GPU and crawling.
- **It is slow.** ~3 tok/s for an 8B on this machine, so a plan takes roughly
  10 minutes. Hosted inference is the answer if that matters — the model is a
  config string.

### Things only a live run finds

Every one of these passed the offline suite and still produced a wrong PDF or a
failed run:

- **Reasoning is on by default and its tokens count against `max_tokens`.**
  Ollama's `think` and OpenRouter's `reasoning` are the same trap: the budget is
  spent before the JSON closes. Both are disabled by default now.
- **OpenRouter reports upstream failures as HTTP 200 with an error body**, so a
  status check does not catch them and the next line is a bare `KeyError` on
  `choices`.
- **Glyphs with no font coverage vanish silently.** A paper read *"Cohen's  of
  0.83"* — XeTeX dropped the kappa, with nothing in the log. Greek and maths
  symbols now go to math mode and anything else folds to ASCII.
- **natbib prints `short` and then the year**, so a model that puts the year in
  `short` gets *"(Patel, 2023, 2023)"*.
- **A model that means "no figure" emits an empty object**, not `null`, because
  the schema offers it the shape.

### Why the queue is a thread pool and a directory

The plan said Redis and arq. That is right for a fleet of workers and wrong
here: it makes running the thing at all require standing up a broker, for a
workload of a handful of long HTTP calls and one subprocess.

Each job is a directory holding its own status file and artifacts, which the
pipeline was already writing. So the queue is a thread pool, the state is on
disk, and finished work survives a restart. If this ever needs more than one
machine, swapping [jobs.py](rhetoric/jobs.py) for arq is the whole migration.

### Why prose is one call per section

A model asked for two thousand words of fabricated methodology in one go loses
track of its own study halfway through, and every section after that quietly
contradicts the ones before. So each section gets its own call, handed the
pinned `dataset` and — where the section has one — the figure's actual numbers,
so Results quotes the bars it is describing rather than inventing a second set.

The calls are independent, so they run concurrently (`--workers`, default 4)
and wall-clock time is roughly one section rather than seven.

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

This is the single biggest quality problem in the project, and it took two
rounds to get right.

Given a complete worked example, qwen3:8b returned a hot dog paper written by
the cereal exemplar's authors, at its institutions, with its sample size,
effect size and citation keys intact — while the prompt was explicitly telling
it not to reuse any of them. Fixing the names was not enough: the first hosted
run then reproduced **5 of the 7 register example sentences word-for-word**,
so the abstract was largely stitched together from the prompt.

Three things fixed it:

1. **The examples are domain-locked.** They are now fragments about mineral
   classification, so lifting one into a paper about food is both caught and
   obviously wrong. They demonstrate the same moves.
2. **`EXEMPLAR_TOKENS`** rejects distinctive nouns — but *only ones that appear
   in a prompt*, which a test asserts. The list once carried terms from
   `specs/cereal_soup.json`, a reference output the model never sees, so those
   entries could only fire on a coincidence. They did: three runs in a row died
   because the model independently coined the "Journal of Culinary Ontology",
   which is simply what a paper on food taxonomy would cite. **Banning an
   invention is worse than missing a copy** — the retry loop can fix a copy, but
   there is nothing to fix in a coincidence.
3. **Phrase windows**, read from the prompt files themselves so the ban cannot
   drift when the examples are edited.

The phrase check is two tests, because one threshold cannot do both jobs. A
whole example sentence reproduced verbatim is leakage at any length. A long
*span* is leakage even when the surrounding sentence differs. But a short
shared tail is the register working as intended — adapting *"a replication
using X rather than Y would be informative, and we have not conducted one"* to
a new subject keeps a nine-word tail. At an eight-word window that was
rejected, and one Limitations section failed four attempts running on the same
construction and took the whole run down.

If you edit the prompts, add any new distinctive proper nouns to
`EXEMPLAR_TOKENS`. The phrases look after themselves.

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

## What to do next

The thing actually worth doing next: run a few models over the same claims and
read the Methods sections. Deadpan register is where they differ, and it is not
predictable from size or price. `deepseek-v4-flash` is simply the first one
that worked.

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

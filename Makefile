SPEC ?= specs/cereal_soup.json
OUT  ?= build
# Pin the interpreter: an ambient `python3` may be a system Python
# without the deps. `make venv` creates this.
PY   ?= .venv/bin/python

.PHONY: venv paper tex open clean

venv:                 ## create .venv and install deps
	uv venv --python 3.12 && uv pip install -e .

paper:                ## spec -> PDF
	$(PY) -m rhetoric.build --spec $(SPEC) --outdir $(OUT)

tex:                  ## spec -> LaTeX + figures only (no engine needed)
	$(PY) -m rhetoric.build --spec $(SPEC) --outdir $(OUT) --no-pdf

open: paper
	open $(OUT)/*-SATIRE.pdf

clean:
	rm -rf $(OUT)

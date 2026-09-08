SPEC  ?= specs/cereal_soup.json
CLAIM ?= Cereal is a soup
OUT  ?= build
# Pin the interpreter: an ambient `python3` may be a system Python
# without the deps. `make venv` creates this.
PY   ?= .venv/bin/python

.PHONY: venv test write paper tex open clean

venv:                 ## create .venv and install deps
	uv venv --python 3.12 && uv pip install -e . && uv pip install pytest ruff

test:                 ## run the suite (no API key or model needed)
	$(PY) -m pytest -q

write:                ## claim -> plan -> prose -> spec -> PDF (needs an API key)
	@mkdir -p $(OUT)
	$(PY) -m rhetoric.plan    --claim "$(CLAIM)" -o $(OUT)/plan.json
	$(PY) -m rhetoric.prose   --plan $(OUT)/plan.json -o $(OUT)/prose.json
	$(PY) -m rhetoric.compose --plan $(OUT)/plan.json --prose $(OUT)/prose.json -o $(OUT)/spec.json
	$(PY) -m rhetoric.build   --spec $(OUT)/spec.json --outdir $(OUT)

paper:                ## spec -> PDF
	$(PY) -m rhetoric.build --spec $(SPEC) --outdir $(OUT)

tex:                  ## spec -> LaTeX + figures only (no engine needed)
	$(PY) -m rhetoric.build --spec $(SPEC) --outdir $(OUT) --no-pdf

open: paper
	open $(OUT)/*-SATIRE.pdf

clean:
	rm -rf $(OUT)

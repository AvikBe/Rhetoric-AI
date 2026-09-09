"""Stage 4: the HTTP service.

A paper takes about ninety seconds, so nothing is generated inside a request.
POST returns a job id immediately and the work happens on a background thread;
poll the job, then fetch the PDF.

    make serve
    curl -X POST localhost:8000/papers -H 'content-type: application/json' \\
         -d '{"claim": "A hot dog is a sandwich"}'

Note what is deliberately *not* offered: there is no way to turn the disclosure
layer off. `disclosure` chooses how loud the markings are, never whether they
exist. Serving this makes it easy to hand someone a paper that looks real, and
the markings are what keep that a joke rather than a forgery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .jobs import Job, JobStore
from .llm import ModelConfig, ModelError
from .pipeline import MAX_CLAIM_CHARS, write

app = FastAPI(
    title="Rhetoric-AI",
    description="Generates satirical arXiv-style preprints. Every output is marked as fiction.",
    version="0.1.0",
)
store = JobStore()


class PaperRequest(BaseModel):
    claim: str = Field(min_length=3, max_length=MAX_CLAIM_CHARS, examples=["A hot dog is a sandwich"])
    # No "none": see the module docstring.
    disclosure: Literal["subtle", "loud"] = "subtle"


class JobView(BaseModel):
    id: str
    status: str
    stage: str
    claim: str
    created_at: str
    updated_at: str
    title: str | None = None
    words: int | None = None
    error: str | None = None
    pdf_url: str | None = None

    @classmethod
    def of(cls, job: Job) -> JobView:
        return cls(
            **{k: getattr(job, k) for k in
               ("id", "status", "stage", "claim", "created_at", "updated_at",
                "title", "words", "error")},
            pdf_url=f"/papers/{job.id}/pdf" if job.pdf else None,
        )


def _work(job: Job, outdir: Path) -> dict:
    paper = write(
        job.claim,
        outdir,
        ModelConfig.from_env(),
        disclosure=job.disclosure,
        on_progress=lambda stage: store.update(job.id, stage=stage),
    )
    return {"title": paper.title, "words": paper.words, "pdf": paper.pdf.name}


@app.get("/healthz")
def healthz() -> dict:
    """Also reports whether a model is reachable, since that is the usual cause."""
    try:
        cfg = ModelConfig.from_env()
    except ModelError as exc:
        return {"ok": False, "model": None, "detail": str(exc)}
    return {"ok": True, "model": f"{cfg.provider}:{cfg.model}"}


@app.post("/papers", status_code=202, response_model=JobView)
def create_paper(request: PaperRequest, background: BackgroundTasks) -> JobView:
    job = store.create(request.claim, request.disclosure)
    background.add_task(store.run, job, _work)
    return JobView.of(job)


@app.get("/papers", response_model=list[JobView])
def list_papers(limit: int = 50) -> list[JobView]:
    return [JobView.of(j) for j in store.list(limit)]


@app.get("/papers/{job_id}", response_model=JobView)
def get_paper(job_id: str) -> JobView:
    if (job := store.get(job_id)) is None:
        raise HTTPException(404, "no such job")
    return JobView.of(job)


@app.get("/papers/{job_id}/pdf")
def get_pdf(job_id: str) -> FileResponse:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job.status != "done" or not job.pdf:
        raise HTTPException(409, f"job is {job.status}, not done")

    path = store.root / job.id / job.pdf
    if not path.exists():
        raise HTTPException(410, "the PDF is gone from disk")
    # The filename carries the disclosure too, so keep it on the download.
    return FileResponse(path, media_type="application/pdf", filename=path.name)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

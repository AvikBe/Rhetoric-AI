"""Stage 4: the job store and the HTTP surface.

`pipeline.write` is stubbed throughout, so none of this needs a key or a model.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rhetoric import app as app_mod
from rhetoric.jobs import Job, JobStore
from rhetoric.pipeline import MAX_CLAIM_CHARS, Paper


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(root=tmp_path / "jobs")


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A client whose jobs land in tmp_path and whose pipeline is fake."""
    monkeypatch.setattr(app_mod, "store", JobStore(root=tmp_path / "jobs"))

    def fake_write(claim, outdir, cfg, *, disclosure="subtle", on_progress=None, **kw):
        outdir.mkdir(parents=True, exist_ok=True)
        pdf = outdir / "a-claim-SATIRE.pdf"
        pdf.write_bytes(b"%PDF-1.7\nfake\n%%EOF\n")
        if on_progress:
            on_progress("planning")
        return Paper(plan=_FakePlan(), pdf=pdf, words=1234)

    monkeypatch.setattr(app_mod, "write", fake_write)
    monkeypatch.setattr(app_mod, "ModelConfig", _FakeConfig)
    return TestClient(app_mod.app)


class _FakePlan:
    title = "A Structural Reanalysis"


class _FakeConfig:
    provider, model = "openrouter", "fake"

    @classmethod
    def from_env(cls):
        return cls()


class TestJobStore:
    def test_survives_a_restart(self, tmp_path: Path) -> None:
        """State is on disk, so a new process sees finished work."""
        first = JobStore(root=tmp_path / "jobs")
        job = first.create("A hot dog is a sandwich")
        first.update(job.id, status="done", title="T", pdf="p.pdf")

        reopened = JobStore(root=tmp_path / "jobs")
        assert (found := reopened.get(job.id)) is not None
        assert found.status == "done" and found.title == "T"

    def test_unknown_id_is_none(self, store: JobStore) -> None:
        assert store.get("nope") is None

    def test_traversal_is_refused(self, store: JobStore) -> None:
        """Ids are generated, not taken from the path -- but do not rely on it."""
        assert store.get("../../etc") is None

    def test_failure_is_recorded_not_raised(self, store: JobStore) -> None:
        job = store.create("claim")

        def boom(job: Job, outdir: Path) -> dict:
            raise RuntimeError("the model exploded")

        store.run(job, boom)  # must not propagate

        assert (done := store.get(job.id)) is not None
        assert done.status == "failed"
        assert "exploded" in (done.error or "")

    def test_success_records_the_result(self, store: JobStore) -> None:
        job = store.create("claim")
        store.run(job, lambda j, d: {"title": "T", "words": 10, "pdf": "x.pdf"})

        assert (done := store.get(job.id)) is not None
        assert done.status == "done" and done.words == 10

    def test_concurrency_is_bounded(self, tmp_path: Path) -> None:
        import threading

        store = JobStore(root=tmp_path / "jobs", workers=2)
        live, peak, lock = [0], [0], threading.Lock()

        def slow(job: Job, outdir: Path) -> dict:
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.1)
            with lock:
                live[0] -= 1
            return {}

        threads = [threading.Thread(target=store.run, args=(store.create("c"), slow))
                   for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert peak[0] <= 2, f"ran {peak[0]} jobs at once with workers=2"

    def test_listing_is_newest_first(self, store: JobStore) -> None:
        ids = []
        for _ in range(3):
            ids.append(store.create("c").id)
            time.sleep(1.05)  # timestamps are second-resolution
        assert [j.id for j in store.list()] == list(reversed(ids))


class TestApi:
    def test_post_returns_202_and_completes(self, client: TestClient) -> None:
        response = client.post("/papers", json={"claim": "A hot dog is a sandwich"})
        assert response.status_code == 202
        job = response.json()
        assert job["status"] in ("queued", "running", "done")

        # TestClient runs background tasks before returning, so it is done.
        final = client.get(f"/papers/{job['id']}").json()
        assert final["status"] == "done"
        assert final["title"] == "A Structural Reanalysis"
        assert final["pdf_url"] == f"/papers/{job['id']}/pdf"

    def test_pdf_download(self, client: TestClient) -> None:
        job = client.post("/papers", json={"claim": "A hot dog is a sandwich"}).json()
        pdf = client.get(f"/papers/{job['id']}/pdf")

        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")
        # The filename is part of the disclosure layer.
        assert "SATIRE" in pdf.headers["content-disposition"]

    def test_pdf_before_completion_is_409(self, client: TestClient, tmp_path: Path) -> None:
        job = app_mod.store.create("A hot dog is a sandwich")
        response = client.get(f"/papers/{job.id}/pdf")
        assert response.status_code == 409

    def test_unknown_job_is_404(self, client: TestClient) -> None:
        assert client.get("/papers/deadbeef").status_code == 404
        assert client.get("/papers/deadbeef/pdf").status_code == 404

    def test_disclosure_cannot_be_disabled(self, client: TestClient) -> None:
        """The API exposes how loud the markings are, never whether they exist."""
        assert client.post("/papers", json={"claim": "c" * 10, "disclosure": "none"}).status_code == 422
        assert client.post("/papers", json={"claim": "c" * 10, "disclosure": "loud"}).status_code == 202

    def test_claim_is_bounded(self, client: TestClient) -> None:
        assert client.post("/papers", json={"claim": "no"}).status_code == 422
        long = "x" * (MAX_CLAIM_CHARS + 1)
        assert client.post("/papers", json={"claim": long}).status_code == 422

    def test_healthz_reports_the_model(self, client: TestClient) -> None:
        body = client.get("/healthz").json()
        assert body["ok"] is True and body["model"] == "openrouter:fake"

    def test_listing(self, client: TestClient) -> None:
        client.post("/papers", json={"claim": "A hot dog is a sandwich"})
        client.post("/papers", json={"claim": "Cereal is a soup"})
        assert len(client.get("/papers").json()) == 2

"""A job store on the filesystem.

The roadmap said Redis and arq. That is the right answer for a fleet of workers
and the wrong one here: it makes running the thing at all require standing up a
broker, for a workload of a handful of long HTTP calls and one subprocess.

Each job is a directory holding its own status file and artifacts, which the
pipeline was already writing. So the queue is a thread pool, the state is on
disk, and finished work survives a restart. If this ever needs more than one
machine, swapping this module for arq is the whole migration.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Status = Literal["queued", "running", "done", "failed"]

JOBS_DIR = Path(os.environ.get("RHETORIC_JOBS_DIR", "jobs")).resolve()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    claim: str
    disclosure: str
    status: Status = "queued"
    stage: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    title: str | None = None
    words: int | None = None
    pdf: str | None = None
    error: str | None = None

    @property
    def dir(self) -> Path:
        return JOBS_DIR / self.id


class JobStore:
    """Filesystem-backed, thread-safe. One directory per job."""

    def __init__(self, root: Path | None = None, workers: int = 2) -> None:
        self.root = (root or JOBS_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(workers)

    # -------------------------------------------------------------- state

    def _path(self, job_id: str) -> Path:
        # job ids are generated here, never taken from a request path, but keep
        # the traversal guard anyway so that stays true if a caller changes.
        resolved = (self.root / job_id / "job.json").resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"job id escapes the store: {job_id!r}")
        return resolved

    def _save(self, job: Job) -> None:
        job.updated_at = _now()
        path = self._path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a reader must never see a half-written status.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job), indent=2), encoding="utf-8")
        tmp.replace(path)

    def create(self, claim: str, disclosure: str = "subtle") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], claim=claim.strip(), disclosure=disclosure)
        with self._lock:
            self._save(job)
        return job

    def get(self, job_id: str) -> Job | None:
        try:
            path = self._path(job_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        return Job(**json.loads(path.read_text(encoding="utf-8")))

    def list(self, limit: int = 50) -> list[Job]:
        jobs = [j for d in self.root.iterdir() if d.is_dir() and (j := self.get(d.name))]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)[:limit]

    def update(self, job_id: str, **fields) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            for key, value in fields.items():
                setattr(job, key, value)
            self._save(job)
            return job

    # -------------------------------------------------------------- running

    def run(self, job: Job, work: Callable[[Job, Path], dict]) -> None:
        """Execute `work` in this thread, recording status either way.

        `work` returns the fields to merge into the finished job.
        """
        with self._semaphore:
            try:
                self.update(job.id, status="running", stage="starting")
                result = work(job, self.root / job.id)
                self.update(job.id, status="done", stage="done", **result)
            except Exception as exc:  # noqa: BLE001 -- the failure belongs in the job
                # Full traceback to the log, one line to the caller: the message
                # may quote model output, and the trace is not the user's problem.
                traceback.print_exc()
                self.update(job.id, status="failed", stage="failed", error=str(exc)[:500])

"""
Simple JSON-file-backed state checkpointing, one file per job.

Deliberately not a database — this is a single-user, single-machine tool,
and a plain JSON file is something you can open and read by hand when
debugging, which matters a lot at the beta stage.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any

STATE_DIR = Path(__file__).parent / "jobs"
STATE_DIR.mkdir(exist_ok=True)


@dataclass
class JobState:
    job_id: str
    step: int = 0  # 0 = not started
    status: str = "pending"  # pending | running | error | done
    error_message: str | None = None
    raw_clip_paths: list[str] = field(default_factory=list)
    synced_sequence_id: str | None = None
    transcript: dict[str, Any] | None = None
    deletions: list[dict[str, Any]] | None = None
    log: list[str] = field(default_factory=list)

    def path(self) -> Path:
        return STATE_DIR / f"{self.job_id}.json"

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))

    def note(self, message: str) -> None:
        self.log.append(message)
        self.save()


def load(job_id: str) -> JobState:
    p = STATE_DIR / f"{job_id}.json"
    if not p.exists():
        raise FileNotFoundError(f"No job found with id {job_id}")
    data = json.loads(p.read_text())
    return JobState(**data)


def load_or_init(job_id: str, raw_clip_paths: list[str] | None = None) -> JobState:
    p = STATE_DIR / f"{job_id}.json"
    if p.exists():
        return load(job_id)
    state = JobState(job_id=job_id, raw_clip_paths=raw_clip_paths or [])
    state.save()
    return state


def list_jobs() -> list[dict[str, Any]]:
    jobs = []
    for f in sorted(STATE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = json.loads(f.read_text())
        jobs.append({"job_id": data["job_id"], "step": data["step"], "status": data["status"]})
    return jobs

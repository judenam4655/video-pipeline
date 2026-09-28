"""
Main orchestrator + thin control webpage.

The user prepares everything in Premiere BEFORE starting a job: opens their
project, syncs the clips, selects the primary clip in the Project panel (it
must already have a native transcript), and has the right sequence active.
The panel operates on whatever project is currently open, so no project
path or file upload is needed.

Deterministic sequence; the only AI judgment call is detect_deletions():

  1. get_transcript     (Premiere MCP, native word-level transcript w/ disfluency tags)
  2. detect_deletions    (direct Claude API call)
  3. place_markers        (Premiere MCP, one call per deletion)

Filler-word cleanup is a manual pass the editor does independently in
Premiere's Text-Based Editing panel. `remove_fillers` and `sync_clips` MCP
tools remain in index.js but are intentionally unwired.

State is checkpointed to JSON after every step; resuming a job re-runs from
the last completed step.

Run:  ./start.sh   (or: uvicorn main:app --reload --port 8000)
"""

import asyncio
import time
import uuid
from collections import Counter
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

import state as state_mod
from mcp_client import McpServers
from removal_agent import detect_deletions
from transcript_converter import convert_transcript

app = FastAPI(title="Video Pipeline Orchestrator")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.post("/jobs")
async def create_job():
    """Starts a new job against the currently open Premiere project."""
    job_id = str(uuid.uuid4())[:8]
    job = state_mod.load_or_init(job_id)
    job.status = "running"
    job.save()

    asyncio.create_task(run_pipeline(job_id))
    return {"job_id": job_id}


@app.get("/jobs")
async def list_jobs():
    return state_mod.list_jobs()


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = state_mod.load(job_id)
    return {
        "job_id": job.job_id,
        "step": job.step,
        "status": job.status,
        "error_message": job.error_message,
        "log": job.log,
        "num_deletions": len(job.deletions) if job.deletions else 0,
    }


@app.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str):
    """Re-runs a failed job from its last checkpointed step."""
    job = state_mod.load(job_id)
    job.status = "running"
    job.error_message = None
    job.note("Resuming.")
    asyncio.create_task(run_pipeline(job_id))
    return {"resumed": job_id, "from_step": job.step}


STEP_NAMES = {
    1: "get_transcript",
    2: "detect_deletions",
    3: "place_markers",
}


def _flatten(exc):
    """Unwraps (nested) ExceptionGroups so the real error message is visible."""
    subs = getattr(exc, "exceptions", None)
    if subs:
        for s in subs:
            yield from _flatten(s)
    else:
        yield exc


def fmt_ts(sec) -> str:
    """Seconds -> MM:SS.ss (matches how you'd read the Premiere timeline)."""
    m, s = divmod(float(sec), 60)
    return f"{int(m):02d}:{s:05.2f}"


async def run_pipeline(job_id: str):
    job = state_mod.load(job_id)
    stage = "connect_premiere"
    t_pipeline = time.monotonic()

    try:
        async with McpServers() as servers:
            job.note(f"=== Pipeline start (job {job_id}, resuming after step {job.step}/3) ===")

            # Fail fast (with a clear error) if the panel isn't connected
            # or no project/sequence is open.
            t = time.monotonic()
            info = await servers.premiere.call_tool_json("get_active_sequence_info", {})
            seq_dur = info.get("durationSeconds")
            job.note(
                f"[connect] Premiere OK in {time.monotonic() - t:.1f}s | sequence '{info.get('name')}' | "
                f"length {fmt_ts(seq_dur) if seq_dur is not None else '?'} | "
                f"video tracks {info.get('videoTrackCount')}, audio tracks {info.get('audioTrackCount')}"
            )
            stage = None

            if job.step < 1:
                stage = "get_transcript"
                job.note("[get_transcript] fetching transcript of the selected Project-panel clip...")
                t = time.monotonic()
                raw_transcript = await servers.premiere.call_tool_json("get_transcript", {})
                native = raw_transcript.get("segments", [])
                n_words = sum(len(sg.get("words", [])) for sg in native)
                job.note(
                    f"[get_transcript] clip '{raw_transcript.get('clip_name')}' | "
                    f"language {raw_transcript.get('language')} | native segments {len(native)} | "
                    f"words {n_words} | fetched in {time.monotonic() - t:.1f}s"
                )

                segments, disfluency_spans = convert_transcript(raw_transcript)
                if not segments:
                    raise ValueError("Transcript is empty after conversion (no usable words).")

                longest = max(sg["end"] - sg["start"] for sg in segments)
                total_chars = sum(len(sg["text"]) for sg in segments)
                t_first, t_last = segments[0]["start"], segments[-1]["end"]
                job.note(
                    f"[get_transcript] converted -> {len(segments)} segments | spoken span "
                    f"{fmt_ts(t_first)}-{fmt_ts(t_last)} | longest segment {longest:.1f}s | "
                    f"{len(disfluency_spans)} filler words stripped | {total_chars} chars of text"
                )
                if seq_dur is not None and t_last > seq_dur + 1:
                    job.note(
                        f"[get_transcript] WARNING: transcript ends at {fmt_ts(t_last)} but the sequence is only "
                        f"{fmt_ts(seq_dur)} — the clip may not start at 00:00:00, so markers could be misaligned."
                    )

                job.transcript = {"segments": segments, "disfluency_spans": disfluency_spans}
                job.step = 1
                job.note("[get_transcript] done (checkpoint saved)")

            if job.step < 2:
                stage = "detect_deletions"
                job.note("[detect_deletions] starting")
                t = time.monotonic()
                # Blocking Anthropic call; run in a thread so it doesn't stall the event loop.
                deletions = await asyncio.to_thread(detect_deletions, job.transcript["segments"], job.note)
                job.deletions = deletions
                by_rule = ", ".join(f"{r}={c}" for r, c in Counter(d["rule_id"] for d in deletions).items()) or "none"
                job.note(f"[detect_deletions] {len(deletions)} candidates flagged ({by_rule}) in {time.monotonic() - t:.1f}s total")
                for i, d in enumerate(deletions, 1):
                    dur = d["end_ts"] - d["start_ts"]
                    job.note(
                        f"[detect_deletions]   #{i} {d['rule_id']} {fmt_ts(d['start_ts'])}-{fmt_ts(d['end_ts'])} "
                        f"({dur:.1f}s): {d['reasoning'][:90]}"
                    )
                job.step = 2
                job.note("[detect_deletions] done (checkpoint saved)")

            if job.step < 3:
                stage = "place_markers"
                total = len(job.deletions)
                job.note(f"[place_markers] placing {total} markers on the active sequence...")
                for i, d in enumerate(job.deletions, 1):
                    t = time.monotonic()
                    try:
                        result = await servers.premiere.call_tool_json(
                            "place_marker",
                            {
                                "startSeconds": d["start_ts"],
                                "endSeconds": d["end_ts"],
                                "ruleType": d["rule_id"],
                                "reasoning": d["reasoning"],
                            },
                        )
                    except Exception as e:
                        job.note(
                            f"[place_markers] {i}/{total} FAILED {d['rule_id']} "
                            f"{fmt_ts(d['start_ts'])}-{fmt_ts(d['end_ts'])}: {e}"
                        )
                        raise
                    job.note(
                        f"[place_markers] {i}/{total} {d['rule_id']} {fmt_ts(d['start_ts'])}-{fmt_ts(d['end_ts'])} "
                        f"placed in {time.monotonic() - t:.1f}s -> {result}"
                    )
                job.step = 3
                job.note(
                    "[place_markers] done — candidates are now markers in Premiere. "
                    "Filler cleanup + marker review happen independently from here."
                )

            job.status = "done"
            job.save()
            job.note(f"=== Pipeline finished in {time.monotonic() - t_pipeline:.1f}s ===")

    except Exception as e:
        job.status = "error"
        label = stage or STEP_NAMES.get(job.step + 1, '?')
        detail = "; ".join(f"{type(x).__name__}: {x}" for x in _flatten(e))
        job.error_message = f"Failed at step {label}: {detail}"
        job.note(job.error_message)
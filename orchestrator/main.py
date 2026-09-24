"""
Main orchestrator + thin control webpage.

Design intent (per earlier discussion): this is a DETERMINISTIC script, not
an autonomous agent loop. The only AI judgment call is inside
removal_agent.detect_deletions(); everything else is fixed sequence:

  1. sync_clips           (Premiere MCP)
  2. remove_fillers        (Premiere MCP, native)
  3. transcribe             (Transcriber MCP)
  4. detect_deletions       (direct Claude API call)
  5. place markers           (Premiere MCP, one call per deletion)

State is checkpointed to a JSON file after every step, so a crash or a
Premiere hang doesn't force a full restart — rerunning the same job_id
resumes from the last completed step.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=...
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000
"""

import asyncio
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import state as state_mod
from mcp_client import McpServers
from removal_agent import detect_deletions

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Video Pipeline Orchestrator")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.post("/jobs")
async def create_job(files: list[UploadFile] = File(...)):
    """Accepts exactly 3 raw clips, starts a new job, kicks off the pipeline in the background."""
    if len(files) != 3:
        return JSONResponse({"error": "Expected exactly 3 clip files"}, status_code=400)

    job_id = str(uuid.uuid4())[:8]
    saved_paths = []
    for f in files:
        dest = UPLOAD_DIR / f"{job_id}_{f.filename}"
        dest.write_bytes(await f.read())
        saved_paths.append(str(dest))

    job = state_mod.load_or_init(job_id, raw_clip_paths=saved_paths)
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
    """Re-runs a job from its last checkpointed step — use after fixing whatever caused a failure."""
    job = state_mod.load(job_id)
    job.status = "running"
    job.error_message = None
    job.save()
    asyncio.create_task(run_pipeline(job_id))
    return {"resumed": job_id, "from_step": job.step}


STEP_NAMES = {
    1: "sync_clips",
    2: "remove_fillers",
    3: "transcribe",
    4: "detect_deletions",
    5: "place_markers",
}


async def run_pipeline(job_id: str):
    job = state_mod.load(job_id)

    try:
        async with McpServers() as servers:

            if job.step < 1:
                job.note("Starting: sync_clips")
                result = await servers.premiere.call_tool_json(
                    "sync_clips", {"clipPaths": job.raw_clip_paths}
                )
                job.synced_sequence_id = result.get("sequenceId")
                job.step = 1
                job.note(f"Done: sync_clips -> sequence {job.synced_sequence_id}")

            if job.step < 2:
                job.note("Starting: remove_fillers")
                await servers.premiere.call_tool_json(
                    "remove_fillers", {"sequenceId": job.synced_sequence_id}
                )
                job.step = 2
                job.note("Done: remove_fillers")

            if job.step < 3:
                job.note("Starting: transcribe")
                # NOTE: transcriber currently expects an audio/video file path,
                # not a Premiere sequence id — you'll likely need an extra
                # "export sequence audio" Premiere MCP call before this, or
                # have remove_fillers/sync_clips return an exported path
                # directly. Left as-is for the beta skeleton; revisit once
                # the Premiere MCP tools are actually implemented.
                transcript = await servers.transcriber.call_tool_json(
                    "transcribe", {"sequence_audio_path": job.synced_sequence_id}
                )
                job.transcript = transcript
                job.step = 3
                job.note(f"Done: transcribe -> {len(transcript.get('segments', []))} segments")

            if job.step < 4:
                job.note("Starting: detect_deletions")
                deletions = detect_deletions(job.transcript["segments"])
                job.deletions = deletions
                job.step = 4
                job.note(f"Done: detect_deletions -> {len(deletions)} candidates flagged")

            if job.step < 5:
                job.note("Starting: place_markers")
                for d in job.deletions:
                    await servers.premiere.call_tool_json(
                        "place_marker",
                        {
                            "startSeconds": d["start_ts"],
                            "endSeconds": d["end_ts"],
                            "ruleType": d["rule_id"],
                            "reasoning": d["reasoning"],
                        },
                    )
                job.step = 5
                job.note("Done: place_markers — all candidates are now visible as markers in Premiere.")

            job.status = "done"
            job.save()

    except Exception as e:
        job.status = "error"
        job.error_message = f"Failed at step {STEP_NAMES.get(job.step + 1, '?')}: {e}"
        job.note(job.error_message)

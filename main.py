from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel
from typing import List, Optional
import uuid
import os
import asyncio
import json
import shutil
import aiofiles
from pathlib import Path
import time
from services.video import process_video_task

app = FastAPI()

# In-memory job store
jobs = {}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


JOB_RETENTION_SECONDS = _env_int("JOB_RETENTION_SECONDS", 15 * 60)  # default 15 minutes
CLEANUP_INTERVAL_SECONDS = _env_int("CLEANUP_INTERVAL_SECONDS", 60)  # default 60 seconds
DELETE_OUTPUT_AFTER_DOWNLOAD = os.getenv("DELETE_OUTPUT_AFTER_DOWNLOAD", "1").lower() not in {"0", "false", "no"}

class NarrationSegment(BaseModel):
    start: str
    end: str
    text: str

class JobStatus(BaseModel):
    job_id: str
    status: str
    message: Optional[str] = None

@app.post("/narrate")
async def create_narration_job(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    video_url: Optional[str] = Form(None),
    script: str = Form(...),
    voice: str = Form("pt-BR-AntonioNeural"),
    add_subtitles: bool = Form(False)
):
    if not file and not video_url:
        raise HTTPException(status_code=400, detail="Either 'file' or 'video_url' must be provided.")

    try:
        script_data = json.loads(script)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'script' field.")

    job_id = str(uuid.uuid4())
    now = time.time()
    jobs[job_id] = {
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "output_path": None,
        "message": None,
    }

    # Handle Input Source
    video_source = {}
    if file:
        temp_input_path = Path("temp") / f"upload_{job_id}_{file.filename}"
        temp_input_path.parent.mkdir(exist_ok=True)

        # Stream file to disk in chunks to avoid memory issues
        async with aiofiles.open(temp_input_path, 'wb') as out_file:
            while content := await file.read(1024 * 1024):  # Read 1MB chunks
                await out_file.write(content)

        video_source = {"type": "file", "value": str(temp_input_path)}
    else:
        video_source = {"type": "url", "value": video_url}

    # Define the background task wrapper to update status
    async def run_task(job_id, source, script, options):
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["updated_at"] = time.time()

        success, error, output_path = await process_video_task(job_id, source, script, options)
        if success:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["output_path"] = output_path
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["message"] = error

        jobs[job_id]["updated_at"] = time.time()

        # Cleanup upload if needed
        if source['type'] == 'file' and os.path.exists(source['value']):
            os.remove(source['value'])

    options = {
        "voice": voice,
        "add_subtitles": add_subtitles
    }

    background_tasks.add_task(run_task, job_id, video_source, script_data, options)

    return {"job_id": job_id, "status": "queued"}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.get("/download/{job_id}")
async def download_video(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    if jobs[job_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail=f"Job is {jobs[job_id]['status']}")

    file_path = jobs[job_id].get("output_path") or f"output/{job_id}.mp4"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="File missing on server")

    def _delete_after_send(path: str, jid: str):
        try:
            if os.path.exists(path):
                os.remove(path)
        finally:
            # Drop job metadata after first download in automated flows.
            jobs.pop(jid, None)

    background = BackgroundTask(_delete_after_send, file_path, job_id) if DELETE_OUTPUT_AFTER_DOWNLOAD else None
    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=f"narrated_{job_id}.mp4",
        background=background,
    )


async def _cleanup_loop():
    while True:
        try:
            now = time.time()

            # Remove stale jobs + any leftover output files.
            for jid, data in list(jobs.items()):
                updated_at = float(data.get("updated_at") or data.get("created_at") or now)
                if now - updated_at < JOB_RETENTION_SECONDS:
                    continue

                output_path = data.get("output_path")
                if output_path and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except Exception:
                        pass
                jobs.pop(jid, None)

            # Best-effort cleanup of temp/ and output/ stray files older than retention.
            for base in (Path("temp"), Path("output")):
                if not base.exists():
                    continue
                for p in base.iterdir():
                    try:
                        mtime = p.stat().st_mtime
                        if now - mtime < JOB_RETENTION_SECONDS:
                            continue
                        if p.is_dir():
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            p.unlink(missing_ok=True)
                    except Exception:
                        continue
        finally:
            await asyncio.sleep(max(5, CLEANUP_INTERVAL_SECONDS))


@app.on_event("startup")
async def _startup():
    app.state.cleanup_task = asyncio.create_task(_cleanup_loop())


@app.on_event("shutdown")
async def _shutdown():
    task = getattr(app.state, "cleanup_task", None)
    if task:
        task.cancel()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "1").lower() not in {"0", "false", "no"}

    kwargs = {"host": host, "port": port, "reload": reload}
    if reload:
        kwargs["reload_excludes"] = ["temp/*", "output/*", "temp", "output"]

    uvicorn.run("main:app", **kwargs)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import uuid
import os
import asyncio
import json
import shutil
import aiofiles
from pathlib import Path
from services.video import process_video_task

app = FastAPI()

# In-memory job store
jobs = {}

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
    jobs[job_id] = {"status": "queued"}

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
        success, error = await process_video_task(job_id, source, script, options)
        if success:
            jobs[job_id]["status"] = "completed"
        else:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["message"] = error

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

    file_path = f"output/{job_id}.mp4"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="File missing on server")

    return FileResponse(file_path, media_type="video/mp4", filename=f"narrated_{job_id}.mp4")

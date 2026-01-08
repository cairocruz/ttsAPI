import edge_tts
import asyncio
import os
import shutil
from pathlib import Path
import subprocess
import imageio_ffmpeg
import re

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

async def generate_speech(text, voice, output_path):
    """Generates MP3 audio from text using edge-tts."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path

async def get_audio_duration(file_path):
    """Returns the duration of the audio file in seconds."""
    try:
        path = str(file_path)
        if not os.path.exists(path):
            print(f"Error getting duration: file does not exist: {path}")
            return 0.0
        if os.path.getsize(path) == 0:
            print(f"Error getting duration: empty file: {path}")
            return 0.0

        # imageio-ffmpeg only provides ffmpeg; parse duration from ffmpeg banner output.
        # Note: ffmpeg returns a non-zero code when no output is specified; that's OK here.
        cmd = [FFMPEG_EXE, '-hide_banner', '-i', path]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        text = (stderr or b"").decode(errors="replace")
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+)(?:\.(\d+))?", text)
        if not match:
            if "Duration: N/A" in text:
                return 0.0
            print(f"Error getting duration: could not parse duration for {path}")
            return 0.0

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        frac = match.group(4) or "0"
        fraction = float(f"0.{frac}")

        return hours * 3600 + minutes * 60 + seconds + fraction
    except Exception as e:
        print(f"Error getting duration: {e}")
        return 0.0

async def adjust_speed_to_fit(audio_path, max_duration):
    """
    Speeds up the audio file if it is longer than max_duration.
    Returns the path to the (potentially modified) audio file.
    """
    current_duration = await get_audio_duration(audio_path)

    # If it fits or max_duration is invalid, return original
    if current_duration <= max_duration or max_duration <= 0:
        return audio_path

    # Calculate speed factor.
    speed_factor = current_duration / max_duration

    # ffmpeg 'atempo' filter is limited between 0.5 and 2.0.
    if speed_factor > 2.0:
        speed_factor = 2.0

    # Create temp output filename
    p = Path(audio_path)
    output_path = p.with_name(f"{p.stem}_speed{p.suffix}")

    cmd = [
        FFMPEG_EXE, '-y',
        '-i', str(audio_path),
        '-filter:a', f"atempo={speed_factor}",
        '-vn', str(output_path)
    ]

    try:
        # Run ffmpeg asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            return str(output_path)
        else:
            err_text = (stderr or b"").decode(errors="replace")
            print(f"Error adjusting speed: ffmpeg returned {process.returncode}: {err_text}")
            return audio_path
    except Exception as e:
        print(f"Error adjusting speed: {e}")
        return audio_path

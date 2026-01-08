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


async def generate_speech_with_word_boundaries(text, voice, output_path):
    """
    Generates MP3 audio and returns word boundary timings.

    Returns a list of dicts: {"offset_s": float, "duration_s": float, "text": str}
    where offset/duration are relative to the start of this audio.
    """
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    word_boundaries = []

    # edge-tts stream provides both audio chunks and WordBoundary events.
    # Offset/duration are typically provided in 100-nanosecond ticks.
    with open(output_path, "wb") as f:
        async for chunk in communicate.stream():
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                f.write(chunk.get("data", b""))
            elif chunk_type == "WordBoundary":
                offset = chunk.get("offset", 0)
                duration = chunk.get("duration", 0)
                word = chunk.get("text", "")

                # Best-effort conversion: Edge offsets are commonly 100ns ticks.
                offset_s = float(offset) / 10_000_000
                duration_s = float(duration) / 10_000_000
                word_boundaries.append({"offset_s": offset_s, "duration_s": duration_s, "text": word})

    return output_path, word_boundaries

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
        return audio_path, 1.0

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
            return str(output_path), speed_factor
        else:
            err_text = (stderr or b"").decode(errors="replace")
            print(f"Error adjusting speed: ffmpeg returned {process.returncode}: {err_text}")
            return audio_path, 1.0
    except Exception as e:
        print(f"Error adjusting speed: {e}")
        return audio_path, 1.0

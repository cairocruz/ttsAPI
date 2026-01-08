import edge_tts
import asyncio
import os
import shutil
from pathlib import Path
import subprocess
import imageio_ffmpeg

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

async def generate_speech(text, voice, output_path):
    """Generates MP3 audio from text using edge-tts."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    return output_path

async def get_audio_duration(file_path):
    """Returns the duration of the audio file in seconds."""
    cmd = [
        FFMPEG_EXE, '-i', str(file_path), '-show_entries', 'format=duration', '-v', 'quiet', '-of', 'csv=p=0'
    ]
    try:
        # Run blocking subprocess in a thread
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        output = stdout.decode().strip()
        return float(output)
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
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await process.wait()

        if process.returncode == 0:
            return str(output_path)
        else:
            print(f"FFmpeg returned {process.returncode}")
            return audio_path
    except Exception as e:
        print(f"Error adjusting speed: {e}")
        return audio_path

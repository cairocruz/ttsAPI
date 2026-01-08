import os
import json
import asyncio
import subprocess
import requests
import shutil
import math
from pathlib import Path
from services.tts import generate_speech, adjust_speed_to_fit
import imageio_ffmpeg

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

def parse_time_str(time_str):
    """Converts 'MM:SS' or 'HH:MM:SS' string to seconds."""
    parts = list(map(int, time_str.split(':')))
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0

def create_srt_content(script_data):
    """
    Generates SRT formatted string from script data.
    """
    srt_output = ""
    for i, item in enumerate(script_data):
        start_seconds = parse_time_str(item["start"])
        end_seconds = parse_time_str(item["end"])
        text = item["text"]

        # Helper to format seconds back to HH:MM:SS,mmm
        def fmt(seconds):
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds - int(seconds)) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        srt_output += f"{i+1}\n"
        srt_output += f"{fmt(start_seconds)} --> {fmt(end_seconds)}\n"
        srt_output += f"{text}\n\n"

    return srt_output

async def download_file(url, local_filename):
    """Downloads a file from a URL to a local path asynchronously."""
    # Running blocking I/O in a thread
    def _download():
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(local_filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return local_filename

    return await asyncio.to_thread(_download)

async def process_video_task(job_id, video_source, script, options):
    """
    Main processing function.
    video_source: dict with 'type' ('file' or 'url') and 'path' or 'url'.
    script: list of dicts.
    options: dict (voice, add_subtitles, etc).
    """
    work_dir = Path("temp") / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # Ensure output directory exists
    Path("output").mkdir(exist_ok=True)
    output_filename = f"output/{job_id}.mp4"

    try:
        # 1. Prepare Video
        video_path = work_dir / "input_video.mp4"
        if video_source['type'] == 'url':
            await download_file(video_source['value'], video_path)
        else:
            # Assuming it's a file path that was already saved
            # shutil.copy is blocking, run in thread
            await asyncio.to_thread(shutil.copy, video_source['value'], video_path)

        # 2. Generate Audio Segments
        voice = options.get('voice', 'pt-BR-AntonioNeural')
        audio_segments = [] # List of (start_time, audio_path)

        for i, item in enumerate(script):
            start_sec = parse_time_str(item["start"])
            end_sec = parse_time_str(item["end"])
            duration_limit = end_sec - start_sec

            raw_audio_path = work_dir / f"segment_{i}_raw.mp3"

            # Generate TTS
            await generate_speech(item["text"], voice, str(raw_audio_path))

            # Adjust Speed (now async)
            final_audio_path = await adjust_speed_to_fit(str(raw_audio_path), duration_limit)

            audio_segments.append({
                'start': start_sec,
                'end': end_sec,
                'path': final_audio_path
            })

        # 3. Process with FFmpeg (using filter_complex for robust audio mixing and ducking)
        # We will build a complex FFmpeg command.

        cmd = [FFMPEG_EXE, '-y', '-i', str(video_path)]

        # Add audio inputs
        for seg in audio_segments:
            cmd.extend(['-i', seg['path']])

        # Build Filter Complex
        filter_complex = []

        # 3a. Ducking Original Audio
        between_expressions = []
        for seg in audio_segments:
            between_expressions.append(f"between(t,{seg['start']},{seg['end']})")

        if between_expressions:
            combined_between = "+".join(between_expressions)
            ducking_filter = f"[0:a]volume='if({combined_between}, 0.2, 1.0)':eval=frame[bg_audio]"
            filter_complex.append(ducking_filter)
            base_audio_label = "[bg_audio]"
        else:
            base_audio_label = "[0:a]"

        # 3b. Mixing Narrations
        mix_inputs = [base_audio_label]

        for i, seg in enumerate(audio_segments):
            input_idx = i + 1
            delay_ms = int(seg['start'] * 1000)
            filter_complex.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[aud{i}]")
            mix_inputs.append(f"[aud{i}]")

        # Mix all audios
        # Use normalize=0 to prevent volume reduction when mixing multiple inputs
        filter_complex.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0[a_out]")

        # 3c. Subtitles (Optional)
        video_label = "0:v"
        if options.get('add_subtitles'):
            srt_path = work_dir / "subs.srt"
            srt_content = create_srt_content(script)

            # File writing is fast, but technically blocking.
            await asyncio.to_thread(lambda: srt_path.write_text(srt_content, encoding="utf-8"))

            # Viral Style: Larger font (24), Outline (BorderStyle=1) instead of Box
            style = "Fontname=Arial,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=50"
            srt_path_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")

            filter_complex.append(f"[{video_label}]subtitles='{srt_path_escaped}':force_style='{style}'[v_out]")
            map_video = "[v_out]"
        else:
            map_video = "0:v"

        # Assemble Command
        cmd.extend(['-filter_complex', ";".join(filter_complex)])
        cmd.extend(['-map', map_video, '-map', '[a_out]'])

        # Output options
        cmd.extend(['-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(output_filename)])

        print("Running FFmpeg command:", " ".join(cmd))

        # Run FFmpeg asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
             print(f"FFmpeg Error: {stderr.decode()}")
             raise Exception("FFmpeg transcoding failed")

        # Cleanup (async wrapper for rmtree usually not built-in, run in thread)
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)
        return True, None

    except Exception as e:
        print(f"Error processing video: {e}")
        return False, str(e)

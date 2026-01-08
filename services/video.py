import os
import json
import asyncio
import subprocess
import requests
import shutil
import math
from pathlib import Path
from services.tts import generate_speech_with_word_boundaries, adjust_speed_to_fit
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


def _wrap_srt_text(text: str, max_chars_per_line: int = 22, max_lines: int = 2) -> str:
    words = (text or "").split()
    if not words:
        return ""

    lines: list[str] = []
    current = ""
    for w in words:
        candidate = w if not current else f"{current} {w}"
        if len(candidate) <= max_chars_per_line:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = w
        else:
            # Single word longer than max; hard-cut
            lines.append(w[:max_chars_per_line])
            current = w[max_chars_per_line:]

        if len(lines) >= max_lines:
            break

    if len(lines) < max_lines and current:
        lines.append(current)

    return "\n".join(lines[:max_lines]).strip()


def create_transitional_srt_from_script(script_data, words_per_cue: int = 4, min_cue_duration_s: float = 0.45):
    """Creates SRT where each segment is split into multiple short cues across its duration."""

    def fmt(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    items = []
    for seg in script_data:
        start_s = float(parse_time_str(seg["start"]))
        end_s = float(parse_time_str(seg["end"]))
        duration = max(0.0, end_s - start_s)
        words = (seg.get("text") or "").split()
        if duration <= 0 or not words:
            continue

        # Compute number of cues; reduce if segment is too short.
        raw_cues = max(1, math.ceil(len(words) / max(1, words_per_cue)))
        max_cues = max(1, int(duration / min_cue_duration_s))
        cue_count = min(raw_cues, max_cues) if max_cues > 0 else 1
        cue_words = max(1, math.ceil(len(words) / cue_count))

        for i in range(cue_count):
            w_start = i * cue_words
            w_end = min(len(words), (i + 1) * cue_words)
            chunk = " ".join(words[w_start:w_end]).strip()
            if not chunk:
                continue

            cue_start = start_s + (duration * i / cue_count)
            cue_end = start_s + (duration * (i + 1) / cue_count)
            cue_end = min(end_s, max(cue_start + 0.05, cue_end))

            items.append({
                "start_s": cue_start,
                "end_s": cue_end,
                "text": _wrap_srt_text(chunk)
            })

    out = []
    for idx, it in enumerate(items, start=1):
        out.append(str(idx))
        out.append(f"{fmt(it['start_s'])} --> {fmt(it['end_s'])}")
        out.append(it["text"].strip())
        out.append("")
    return "\n".join(out)


def create_transitional_srt_from_audio_segments_word_boundaries(
    audio_segments,
    words_per_cue: int = 4,
    min_cue_duration_s: float = 0.10,
):
    """Creates SRT using Edge TTS word boundaries (best sync)."""

    def fmt(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    items = []
    for seg in audio_segments:
        seg_start = float(seg["start"])
        seg_end = float(seg["end"])
        speed = float(seg.get("speed_factor") or 1.0)
        timing_scale = 1.0 / speed if speed > 0 else 1.0

        boundaries = seg.get("word_boundaries") or []
        words = [b for b in boundaries if (b.get("text") or "").strip()]
        if not words:
            continue

        group = []
        group_start = None
        group_end = None

        def flush():
            nonlocal group, group_start, group_end
            if not group or group_start is None or group_end is None:
                group = []
                group_start = None
                group_end = None
                return

            cue_start = max(seg_start, group_start)
            cue_end = min(seg_end, max(cue_start + min_cue_duration_s, group_end))
            items.append({
                "start_s": cue_start,
                "end_s": cue_end,
                "text": _wrap_srt_text(" ".join(group)),
            })
            group = []
            group_start = None
            group_end = None

        for w in words:
            offset_s = float(w.get("offset_s") or 0.0) * timing_scale
            dur_s = float(w.get("duration_s") or 0.0) * timing_scale
            w_text = (w.get("text") or "").strip()
            if not w_text:
                continue

            w_start = seg_start + offset_s
            w_end = seg_start + offset_s + max(dur_s, min_cue_duration_s)

            if group_start is None:
                group_start = w_start
            group_end = w_end
            group.append(w_text)

            if len(group) >= words_per_cue:
                flush()

        flush()

    if not items:
        return ""

    out = []
    for idx, it in enumerate(items, start=1):
        out.append(str(idx))
        out.append(f"{fmt(it['start_s'])} --> {fmt(it['end_s'])}")
        out.append(it["text"].strip())
        out.append("")
    return "\n".join(out)

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

            # Generate TTS + capture word boundaries for transitional subtitles
            _, word_boundaries = await generate_speech_with_word_boundaries(
                item["text"], voice, str(raw_audio_path)
            )

            if not raw_audio_path.exists() or raw_audio_path.stat().st_size == 0:
                raise Exception(f"TTS generated an empty audio file: {raw_audio_path}")

            # Adjust Speed (now async)
            final_audio_path, speed_factor = await adjust_speed_to_fit(str(raw_audio_path), duration_limit)

            if not os.path.exists(final_audio_path) or os.path.getsize(final_audio_path) == 0:
                raise Exception(f"Audio segment missing/empty after speed adjust: {final_audio_path}")

            audio_segments.append({
                'start': start_sec,
                'end': end_sec,
                'path': final_audio_path,
                'word_boundaries': word_boundaries,
                'speed_factor': speed_factor
            })

        # 3. Process with FFmpeg (using filter_complex for robust audio mixing and ducking)
        # We will build a complex FFmpeg command.

        cmd = [FFMPEG_EXE, '-y', '-i', str(video_path)]

        # Add audio inputs
        for seg in audio_segments:
            cmd.extend(['-i', seg['path']])

        # Build Filter Complex
        filter_complex = []

        # 3a. Keep original audio at constant volume (no ducking)
        # User-requested: always keep background audio at 0.2
        filter_complex.append("[0:a]volume=0.2[bg_audio]")
        base_audio_label = "[bg_audio]"

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

            # Prefer best-sync subtitles from word boundaries; fallback to time-splitting.
            srt_content = create_transitional_srt_from_audio_segments_word_boundaries(audio_segments)
            if not srt_content.strip():
                srt_content = create_transitional_srt_from_script(script)

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
               err_text = (stderr or b"").decode(errors="replace")
               print(f"FFmpeg Error: {err_text}")
               raise Exception(f"FFmpeg transcoding failed: {err_text}")

        return True, None, output_filename

    except Exception as e:
        print(f"Error processing video: {e}")
        return False, str(e), None

    finally:
        # Always remove temp artifacts; output retention is handled elsewhere.
        await asyncio.to_thread(shutil.rmtree, work_dir, ignore_errors=True)

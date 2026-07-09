"""
assembler.py — Video assembly via FFmpeg subprocess with dynamic transitions, narration sync, color grading, and background music.

Pipeline:
  1. ffprobe voiceover WAV for exact duration.
  2. Map narration-synced product image timestamps from word-level Whisper data.
  3. Apply consistent color grading filter (eq & colorbalance) to all visual inputs.
  4. Build crossfade (xfade) transitions between visual segments.
  5. Mix background music at low volume, ducked under the voiceover.
  6. Burn in ASS subtitles and top price banner.
  7. Encode final 1080×1920 MP4 video.
"""

import json
import logging
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def _run(cmd: list[str], stage: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a subprocess command, raise RuntimeError with ffmpeg stderr on failure."""
    logger.debug(f"[{stage}] Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"[{stage}] FFmpeg command failed (exit {result.returncode}).\n"
            f"Command: {' '.join(cmd)}\n"
            f"stderr:\n{stderr_text}"
        )
    return result


def _probe_duration(path: str) -> float:
    """Use ffprobe to return the duration of a media file in seconds."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Media file not found for probing: {path}")

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        stderr_text = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"ffprobe failed on '{path}' (exit {result.returncode}):\n{stderr_text}"
        )

    data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    for stream in data.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])

    cmd2 = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        path,
    ]
    result2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result2.returncode == 0:
        fmt = json.loads(result2.stdout.decode("utf-8", errors="replace")).get("format", {})
        if fmt.get("duration"):
            return float(fmt["duration"])

    raise RuntimeError(f"Could not determine duration for '{path}' via ffprobe.")


def _escape_ass_path(ass_path: str) -> str:
    """Escape ASS file path for FFmpeg's subtitles= filter value."""
    try:
        rel = os.path.relpath(ass_path).replace("\\", "/")
        return rel.replace("'", "\\'")
    except Exception:
        p = os.path.abspath(ass_path).replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            p = p[0] + "\\:" + p[2:]
        p = p.replace("'", "\\'")
        return p


def _escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter value."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    return text


def _find_narration_timestamps(
    word_timestamps: list[dict],
    product_name: str,
    features: list[str] | None = None
) -> list[float]:
    """
    Finds timestamps in narration where product or key feature words are spoken.
    Returns list of start timestamps (seconds).
    """
    if not word_timestamps:
        return []

    stop_words = {"the", "a", "an", "is", "it", "and", "or", "for", "with", "of", "in", "to", "you", "your", "this", "my"}
    raw_keywords = set()
    for w in re.split(r'\W+', product_name.lower()):
        if len(w) > 2 and w not in stop_words:
            raw_keywords.add(w)
    for f in (features or []):
        for w in re.split(r'\W+', f.lower()):
            if len(w) > 2 and w not in stop_words:
                raw_keywords.add(w)

    matched_times = []
    for item in word_timestamps:
        w_clean = re.sub(r'\W+', '', item.get("word", "").lower())
        if w_clean in raw_keywords:
            matched_times.append(float(item.get("start", 0.0)))

    return matched_times


def assemble_video(
    clip_paths: list[str],
    voiceover_path: str,
    ass_path: str,
    price_text: str,
    output_path: str,
    product_image_paths: list[str] | None = None,
    word_timestamps: list[dict] | None = None,
    product_name: str = "",
    features: list[str] | None = None,
) -> str:
    """
    Assemble a finished 1080×1920 MP4 with:
      - Color grading across all visual sources
      - Dynamic crossfade (xfade) transitions
      - Narration-synced product image timing
      - Ducked background music under voiceover
    """
    missing = []
    if not os.path.exists(voiceover_path):
        missing.append(f"voiceover: {voiceover_path}")
    if not os.path.exists(ass_path):
        missing.append(f"ASS captions: {ass_path}")
    if missing:
        raise FileNotFoundError("The following required files are missing:\n  " + "\n  ".join(missing))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    total_duration = _probe_duration(voiceover_path)
    logger.info(f"[assembler] Voiceover duration: {total_duration:.3f}s")
    if total_duration <= 0:
        raise RuntimeError(f"Voiceover duration probe returned {total_duration}s — invalid.")

    valid_images = [img for img in (product_image_paths or []) if os.path.exists(img)]
    valid_broll = [cp for cp in clip_paths if os.path.exists(cp) and _probe_duration(cp) > 0]

    if not valid_images and not valid_broll:
        raise RuntimeError("No valid product images or stock clips available for video assembly.")

    # -----------------------------------------------------------------------
    # Requirement 8: Narration-synced product image timing
    # -----------------------------------------------------------------------
    narration_starts = _find_narration_timestamps(word_timestamps or [], product_name, features)
    logger.info(f"[assembler] Narration keywords matched at timestamps: {narration_starts}")

    visual_items = []
    if valid_images:
        logger.info(f"[assembler] Syncing {len(valid_images)} product image(s) to narration timing.")
        # If narration timestamps exist, place product images around those timestamps
        if narration_starts and len(narration_starts) >= 1:
            # Sort narration timestamps
            narration_starts.sort()
            img_duration = 2.0
            
            # Divide timeline into segments
            t_curr = 0.0
            img_idx = 0
            broll_idx = 0

            for t_sync in narration_starts:
                if t_sync > t_curr + 1.5 and t_sync < total_duration - 2.0:
                    # Fill gap with B-roll
                    gap = t_sync - t_curr
                    if valid_broll:
                        b_path = valid_broll[broll_idx % len(valid_broll)]
                        visual_items.append({"type": "video", "path": b_path, "slot": gap})
                        broll_idx += 1
                        t_curr += gap

                    # Place narration-synced product image
                    img_path = valid_images[img_idx % len(valid_images)]
                    visual_items.append({"type": "image", "path": img_path, "slot": img_duration})
                    img_idx += 1
                    t_curr += img_duration

            # Fill remaining trailing time with B-roll or product image
            if t_curr < total_duration:
                rem = total_duration - t_curr
                if valid_broll:
                    b_path = valid_broll[broll_idx % len(valid_broll)]
                    visual_items.append({"type": "video", "path": b_path, "slot": rem})
                else:
                    img_path = valid_images[img_idx % len(valid_images)]
                    visual_items.append({"type": "image", "path": img_path, "slot": rem})

            # Guard: narration timestamps may have all been filtered by the gap/boundary
            # conditions, leaving visual_items empty. Fall through to standard distribution.
            if not visual_items:
                logger.warning("[assembler] Narration-sync produced no visual segments — falling back to standard distribution.")
                narration_starts = []  # triggers the else branch below

        if not narration_starts:
            # Standard balanced distribution (default or fallback)
            broll_total_time = 3.0 if valid_broll else 0.0
            primary_total_time = max(1.0, total_duration - broll_total_time)
            img_slot = primary_total_time / len(valid_images)

            for img_path in valid_images:
                visual_items.append({"type": "image", "path": img_path, "slot": img_slot})

            if valid_broll:
                broll_slot = broll_total_time / len(valid_broll)
                cutaway_pos = max(1, len(visual_items) // 2)
                for b_idx, b_path in enumerate(valid_broll):
                    visual_items.insert(cutaway_pos + b_idx, {"type": "video", "path": b_path, "slot": broll_slot})
    else:
        slot_duration = total_duration / len(valid_broll)
        for b_path in valid_broll:
            visual_items.append({"type": "video", "path": b_path, "slot": slot_duration})

    if not visual_items:
        raise RuntimeError("[assembler] No visual items were constructed — check product images and B-roll availability.")

    n = len(visual_items)
    W = config.VIDEO_WIDTH    # 1080
    H = config.VIDEO_HEIGHT   # 1920

    # -----------------------------------------------------------------------
    # Requirement 7: Adjust slot durations to account for crossfade (xfade) overlap
    # -----------------------------------------------------------------------
    xfade_dur = 0.25  # seconds
    if n > 1:
        # Extra duration needed across N-1 transitions to maintain exact total_duration
        extra_per_slot = ((n - 1) * xfade_dur) / n
        for item in visual_items:
            item["slot"] += extra_per_slot

    filter_parts: list[str] = []
    
    # -----------------------------------------------------------------------
    # Requirement 5: Color grading filter across mixed sources
    # -----------------------------------------------------------------------
    color_grade = "eq=contrast=1.06:brightness=0.01:saturation=1.1,colorbalance=rs=0.02:gs=0.0:bs=-0.02"

    for i, item in enumerate(visual_items):
        slot = item["slot"]
        src_label = f"[{i}:v]"
        out_label = f"[v{i}]"

        if item["type"] == "image":
            frames = max(30, int(slot * 30))
            kb_filter = (
                f"{src_label}loop=loop=-1:size=1:start=0,"
                f"scale=2000:-1,"
                f"zoompan=z='min(zoom+0.0015,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H},"
                f"fps=30,trim=0:{slot:.6f},setpts=PTS-STARTPTS,format=yuv420p,setsar=1,{color_grade}"
            )
            filter_parts.append(f"{kb_filter}{out_label}")
        else:
            clip_dur = _probe_duration(item["path"])
            if clip_dur >= slot:
                segment_filter = f"{src_label}trim=0:{slot:.6f},setpts=PTS-STARTPTS"
            else:
                loop_count = int(slot / clip_dur) + 1
                segment_filter = f"{src_label}loop={loop_count}:size=32767:start=0,trim=0:{slot:.6f},setpts=PTS-STARTPTS"

            scale_crop = (
                f"scale='if(gt(iw/ih,{W}/{H}),{H}*iw/ih,{W})'"
                f":'if(gt(iw/ih,{W}/{H}),{H},{W}*ih/iw)',"
                f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,"
                f"fps=30,format=yuv420p,setsar=1,{color_grade}"
            )
            filter_parts.append(f"{segment_filter},{scale_crop}{out_label}")

    # -----------------------------------------------------------------------
    # Requirement 7: Crossfade transitions (xfade)
    # -----------------------------------------------------------------------
    if n == 1:
        concat_out = "[vcat]"
        filter_parts.append(f"[v0]copy{concat_out}")
    else:
        cur_label = "[v0]"
        offset = visual_items[0]["slot"] - xfade_dur
        for idx in range(1, n):
            next_label = f"[v{idx}]"
            out_x = f"[x{idx}]" if idx < n - 1 else "[vcat]"
            filter_parts.append(
                f"{cur_label}{next_label}xfade=transition=fade:duration={xfade_dur:.2f}:offset={offset:.3f}{out_x}"
            )
            cur_label = out_x
            if idx < n - 1:
                offset += visual_items[idx]["slot"] - xfade_dur

    # Burn in ASS subtitles
    escaped_ass = _escape_ass_path(ass_path)
    escaped_price = _escape_drawtext(price_text)
    subs_out = "[vsubs]"
    filter_parts.append(f"[vcat]subtitles='{escaped_ass}'{subs_out}")

    # Price overlay (top banner fade-in)
    price_start = max(0.0, total_duration - 3.0)
    price_fade_duration = 0.5
    price_font_size = 72
    price_box_color = "black@0.55"
    price_y = int(H * 0.20)
    price_out = "[vout]"
    alpha_expr = f"if(lt(t-{price_start:.3f},{price_fade_duration}),(t-{price_start:.3f})/{price_fade_duration},1)"

    if os.name == "nt":
        font_filter_arg = "fontfile='C\\:/Windows/Fonts/arial.ttf'"
    elif os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        font_filter_arg = "fontfile='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'"
    else:
        font_filter_arg = "font='Sans'"

    filter_parts.append(
        f"{subs_out}drawtext="
        f"{font_filter_arg}:"
        f"text='{escaped_price}':"
        f"fontsize={price_font_size}:"
        f"fontcolor=white:"
        f"box=1:boxcolor={price_box_color}:boxborderw=18:"
        f"x=(w-text_w)/2:"
        f"y={price_y}:"
        f"enable='gte(t,{price_start:.3f})':"
        f"alpha='{alpha_expr}'"
        f"{price_out}"
    )

    # -----------------------------------------------------------------------
    # Requirement 6: Background music library & volume ducking
    # -----------------------------------------------------------------------
    music_dir = os.path.join("assets", "music")
    music_files = []
    if os.path.exists(music_dir):
        music_files = [
            os.path.join(music_dir, f) for f in os.listdir(music_dir)
            if f.lower().endswith((".wav", ".mp3", ".m4a"))
        ]

    bg_music_path = random.choice(music_files) if music_files else None
    audio_map_label = f"{n}:a"

    if bg_music_path:
        logger.info(f"[assembler] Using background music track: {bg_music_path}")
        music_idx = n + 1
        # Trim and volume-reduce the background track
        filter_parts.append(
            f"[{music_idx}:a]aloop=loop=-1:size=2000000000,atrim=0:{total_duration:.6f},volume=0.15[bg_raw]"
        )
        # Mix with voiceover; voiceover stream is dominant (duration=first)
        filter_parts.append(
            f"[{n}:a][bg_raw]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        audio_map_label = "[aout]"

    full_filter = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"]

    for item in visual_items:
        cmd += ["-i", item["path"]]
    cmd += ["-i", voiceover_path]
    if bg_music_path:
        cmd += ["-i", bg_music_path]

    cmd += [
        "-filter_complex", full_filter,
        "-map", price_out,
        "-map", audio_map_label,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-t", f"{total_duration:.6f}",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"[assembler] Starting FFmpeg encode -> {output_path}")
    _run(cmd, stage="ffmpeg-encode", timeout=600)

    abs_path = os.path.abspath(output_path)
    size_mb = os.path.getsize(abs_path) / (1024 * 1024)
    logger.info(f"[assembler] Assembly complete: {abs_path} ({size_mb:.1f} MB, {total_duration:.2f}s)")
    return abs_path

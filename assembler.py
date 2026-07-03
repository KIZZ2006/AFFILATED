"""
assembler.py — Video assembly via FFmpeg subprocess.

Pipeline:
  1. ffprobe the voiceover WAV for exact duration.
  2. ffprobe each clip for its duration.
  3. Distribute clip budget evenly across clips; loop short clips if necessary.
  4. Build a filtergraph that:
     a) Trims / loops each clip to its allocated slot duration.
     b) Scales + centre-crops every clip to 1080×1920.
     c) Concatenates all processed clips into one video stream.
     d) Burns in the .ass subtitle file.
     e) Overlays a styled price banner for the final 3 seconds (fade-in).
  5. Maps the voiceover WAV as the sole audio track (AAC 192 k).
  6. Encodes to H.264 (yuv420p) MP4 at output_path.

Design constraints that informed implementation:
- The ASS file path is passed to FFmpeg's `subtitles` filter.  On Windows,
  backslashes and colons in drive letters must be escaped; this is handled by
  _escape_ass_path().
- The price_text (e.g. "$19.99") may contain characters that are special in
  FFmpeg's drawtext expression syntax ($ : ' \).  _escape_drawtext() handles
  these.
- All subprocess calls use explicit timeout=300 so the pipeline never hangs.
- stderr is captured; on non-zero returncode the full ffmpeg stderr is included
  in the raised RuntimeError.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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

    # Some containers report duration only at the container level
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
    """
    Escape the ASS file path for FFmpeg's subtitles= filter value.

    Uses a relative path (e.g. output/captions.ass) with forward slashes
    to prevent FFmpeg libass/fontconfig access violation crashes on Windows.
    """
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
    """
    Escape text for FFmpeg drawtext filter value.

    Characters that must be escaped in FFmpeg drawtext text= :
      :   →  \\:
      '   →  \\\'   (but we wrap in single quotes, so use \\')
      \\  →  \\\\
      %   →  %%
    We wrap the entire value in single quotes in the filter string,
    so only  '  and  \  inside the text itself need escaping.
    """
    text = text.replace("\\", "\\\\")  # backslash first
    text = text.replace("'", "\\'")    # single quote
    text = text.replace(":", "\\:")    # colon
    text = text.replace("%", "%%")     # percent
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assemble_video(
    clip_paths: list[str],
    voiceover_path: str,
    ass_path: str,
    price_text: str,
    output_path: str,
) -> str:
    """
    Assemble a finished 1080×1920 MP4 from stock clips, voiceover, ASS captions,
    and a price-text overlay.

    Args:
        clip_paths:     List of local MP4 paths downloaded from Pexels.
        voiceover_path: Path to the Supertonic-generated WAV file.
        ass_path:       Path to the stable-ts .ass subtitle file.
        price_text:     Human-readable price string (e.g. "$19.99").
        output_path:    Destination path for the final MP4.

    Returns:
        Absolute path to the assembled MP4.

    Raises:
        FileNotFoundError: If any required input file is missing.
        RuntimeError:      If ffprobe or ffmpeg fails, with full stderr detail.
    """
    # ------------------------------------------------------------------
    # 0. Validate inputs
    # ------------------------------------------------------------------
    missing = []
    if not os.path.exists(voiceover_path):
        missing.append(f"voiceover: {voiceover_path}")
    if not os.path.exists(ass_path):
        missing.append(f"ASS captions: {ass_path}")
    for cp in clip_paths:
        if not os.path.exists(cp):
            missing.append(f"clip: {cp}")
    if missing:
        raise FileNotFoundError(
            "The following required files are missing:\n  " + "\n  ".join(missing)
        )
    if not clip_paths:
        raise ValueError("clip_paths must contain at least one video file.")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Probe exact voiceover duration
    # ------------------------------------------------------------------
    logger.info(f"[assembler] Probing voiceover duration: {voiceover_path}")
    total_duration = _probe_duration(voiceover_path)
    logger.info(f"[assembler] Voiceover duration: {total_duration:.3f}s")

    if total_duration <= 0:
        raise RuntimeError(f"Voiceover duration probe returned {total_duration}s — invalid.")

    # ------------------------------------------------------------------
    # 2. Probe each clip duration
    # ------------------------------------------------------------------
    clip_durations: list[float] = []
    valid_clips: list[str] = []
    for cp in clip_paths:
        try:
            d = _probe_duration(cp)
            if d > 0:
                clip_durations.append(d)
                valid_clips.append(cp)
            else:
                logger.warning(f"[assembler] Skipping clip with zero/negative duration: {cp}")
        except Exception as e:
            logger.warning(f"[assembler] Could not probe clip '{cp}', skipping: {e}")

    if not valid_clips:
        raise RuntimeError("No valid video clips available after probing durations.")

    n = len(valid_clips)
    logger.info(f"[assembler] {n} valid clip(s) to use over {total_duration:.3f}s timeline.")

    # ------------------------------------------------------------------
    # 3. Compute per-clip allocation (even distribution, handle loops)
    # ------------------------------------------------------------------
    # Each clip gets an equal slice of the total duration.
    slot_duration = total_duration / n  # target seconds per clip slot

    # Price-overlay timing: fade in 0.5 s before the final-3-second mark
    price_start = max(0.0, total_duration - 3.0)  # wall-clock start of overlay
    price_fade_duration = 0.5                       # seconds for opacity ramp-up

    # ------------------------------------------------------------------
    # 4. Build filtergraph
    #
    # Strategy:
    #   For each clip i:
    #     [i:v] → trim(0, slot_i) OR loop to cover slot_i → setpts=PTS-STARTPTS
    #           → scale+crop to 1080×1920 → fps=30 → [vi]
    #   [v0][v1]...[vN] → concat → [vcat]
    #   [vcat] → subtitles filter → [vsubs]
    #   [vsubs] → drawtext (price overlay, final 3s, fade-in) → [vout]
    #   Audio: [N:a] (the voiceover input) → aformat → [aout]
    # ------------------------------------------------------------------

    W = config.VIDEO_WIDTH    # 1080
    H = config.VIDEO_HEIGHT   # 1920

    # Escape the ASS path once
    escaped_ass = _escape_ass_path(ass_path)
    # Escape the price text once
    escaped_price = _escape_drawtext(price_text)

    filter_parts: list[str] = []
    video_segment_labels: list[str] = []

    for i, (clip_path, clip_dur) in enumerate(zip(valid_clips, clip_durations)):
        slot = slot_duration
        src_label = f"[{i}:v]"
        out_label = f"[v{i}]"

        # --- Trim / loop to fill the slot ---
        if clip_dur >= slot:
            # Clip is long enough: just trim from t=0
            segment_filter = (
                f"{src_label}trim=0:{slot:.6f},setpts=PTS-STARTPTS"
            )
        else:
            # Clip is shorter than the slot: loop it enough times then trim
            loop_count = int(slot / clip_dur) + 1  # enough iterations
            segment_filter = (
                f"{src_label}loop={loop_count}:size=32767:start=0,"
                f"trim=0:{slot:.6f},setpts=PTS-STARTPTS"
            )

        # --- Scale and centre-crop to W×H (1080×1920) ---
        # Scale so the SMALLEST dimension fits W or H, then crop the excess.
        # scale=-1:H keeps aspect ratio with H fixed; then crop=W:H centres.
        scale_crop = (
            f"scale='if(gt(iw/ih,{W}/{H}),{H}*iw/ih,{W})'"
            f":'if(gt(iw/ih,{W}/{H}),{H},{W}*ih/iw)',"
            f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,"
            f"fps=30,format=yuv420p"
        )

        filter_parts.append(f"{segment_filter},{scale_crop}{out_label}")
        video_segment_labels.append(out_label)

    # --- Concatenate all video segments ---
    concat_inputs = "".join(video_segment_labels)
    concat_out = "[vcat]"
    filter_parts.append(
        f"{concat_inputs}concat=n={n}:v=1:a=0{concat_out}"
    )

    # --- Burn in ASS subtitles ---
    subs_out = "[vsubs]"
    filter_parts.append(
        f"{concat_out}subtitles='{escaped_ass}'{subs_out}"
    )

    # --- Price overlay (drawtext, final 3 s, fade-in alpha) ---
    #
    # enable expression: show only from price_start to end_of_video
    # alpha expression: ramp from 0→1 over the fade period
    #
    price_font_size = 72
    price_box_color = "black@0.55"  # semi-transparent pill
    # Vertical position: upper-centre (20% from top) so it clears the captions
    price_y = int(H * 0.20)
    price_out = "[vout]"
    alpha_expr = (
        f"if(lt(t-{price_start:.3f},{price_fade_duration}),"
        f"(t-{price_start:.3f})/{price_fade_duration},1)"
    )
    # Default system font selection cross-platform (Windows & Linux)
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

    full_filter = ";".join(filter_parts)

    # ------------------------------------------------------------------
    # 5. Build the full FFmpeg command
    # ------------------------------------------------------------------
    # Input order: clips first (indices 0..n-1), voiceover last (index n)
    cmd = ["ffmpeg", "-y"]

    for cp in valid_clips:
        cmd += ["-i", cp]
    cmd += ["-i", voiceover_path]

    cmd += [
        "-filter_complex", full_filter,
        "-map", price_out,
        "-map", f"{n}:a",  # voiceover audio
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        # Truncate output to voiceover duration so there's never dead air
        "-t", f"{total_duration:.6f}",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"[assembler] Starting FFmpeg encode -> {output_path}")
    logger.debug(f"[assembler] Filter graph:\n{full_filter}")

    _run(cmd, stage="ffmpeg-encode", timeout=600)

    abs_path = os.path.abspath(output_path)
    size_mb = os.path.getsize(abs_path) / (1024 * 1024)
    logger.info(
        f"[assembler] Assembly complete: {abs_path} ({size_mb:.1f} MB, "
        f"{total_duration:.2f}s)"
    )
    return abs_path

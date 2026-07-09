"""
voiceover.py — Human-sounding TTS via edge-tts (neural) with Kokoro local fallback.

Priority chain (mirrors C:\\agents\\tts.py pattern):
  Tier 1: edge-tts en-IN-NeerjaNeural  — Indian English female neural voice (free, no API key)
  Tier 2: edge-tts en-IN-PrabhatNeural — Indian English male neural voice (fallback)
  Tier 3: Kokoro am_michael            — local ONNX model (zero-cost, zero-latency fallback)

Why Indian English voices?
  en-IN-NeerjaNeural / PrabhatNeural are Microsoft's neural TTS models trained on Indian-accented
  English. They sound natural and relatable to the target Amazon India audience — unlike a flat
  ONNX voice or an American accent. This mirrors the same strategy used in C:\\agents\\tts.py.
"""

import asyncio
import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# ── Voice config ──────────────────────────────────────────────────────────────
_VOICE_PRIMARY   = "en-IN-NeerjaNeural"   # Female, natural Indian English
_VOICE_SECONDARY = "en-IN-PrabhatNeural"  # Male, natural Indian English (backup)
_VOICE_RATE      = "+8%"                  # Slightly faster = more energetic ad delivery


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _probe_duration(path: str) -> float:
    """Use ffprobe to get precise audio file duration in seconds."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        path,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode == 0:
            fmt = json.loads(result.stdout.decode("utf-8", errors="replace")).get("format", {})
            if fmt.get("duration"):
                return float(fmt["duration"])
    except Exception as e:
        logger.warning(f"[TTS] ffprobe duration check failed: {e}")
    return 0.0


async def _edge_tts_synthesize(text: str, output_path: str, voice: str, rate: str) -> bool:
    """
    Async synthesis via edge-tts.

    edge-tts always produces MP3 audio internally. If the caller wants a .wav
    output path (as expected by main.py), we save to a temp .mp3 first, then
    convert to .wav via ffmpeg so the rest of the pipeline receives a proper wav.

    Returns True on success, False on any failure.
    """
    try:
        import edge_tts
    except ImportError:
        logger.warning("[TTS] edge-tts not installed. Run: pip install edge-tts")
        return False

    # Determine a temp mp3 path alongside the final output
    base = output_path.rsplit(".", 1)[0] if "." in os.path.basename(output_path) else output_path
    tmp_mp3 = base + "_tmp.mp3"

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(tmp_mp3)

        if not os.path.exists(tmp_mp3) or os.path.getsize(tmp_mp3) == 0:
            logger.warning(f"[TTS] edge-tts ({voice}) produced an empty file.")
            return False

        # Convert to wav if required by the output path
        if output_path.lower().endswith(".wav"):
            conv = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_mp3, output_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            try:
                os.remove(tmp_mp3)
            except OSError:
                pass
            if conv.returncode != 0:
                logger.warning(
                    f"[TTS] ffmpeg wav conversion failed for {voice}: "
                    + conv.stderr.decode("utf-8", errors="replace")[-200:]
                )
                return False
        else:
            # Move mp3 directly to output path (no conversion needed)
            import shutil
            shutil.move(tmp_mp3, output_path)

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    except Exception as e:
        logger.warning(f"[TTS] edge-tts ({voice}) failed: {e}")
        # Clean up temp file on failure
        try:
            if os.path.exists(tmp_mp3):
                os.remove(tmp_mp3)
        except OSError:
            pass
        return False


def _kokoro_synthesize(text: str, output_path: str) -> bool:
    """
    Local Kokoro ONNX synthesis (uses model from C:\\agents\\kokoro-v1.0.onnx).
    Returns True on success, False if Kokoro is not installed or fails.
    """
    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np

        pipeline = KPipeline(lang_code="a")  # 'a' = American English phonetics
        all_audio = []
        for _, _, audio in pipeline(text, voice="am_michael"):
            all_audio.append(audio)

        if not all_audio:
            return False

        combined = np.concatenate(all_audio)
        sf.write(output_path, combined, 24000)
        logger.info("[TTS] Kokoro am_michael synthesis successful.")
        return True

    except ImportError:
        logger.warning("[TTS] Kokoro not installed — skipping local fallback.")
        return False
    except Exception as e:
        logger.warning(f"[TTS] Kokoro fallback failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_voiceover(script: str, output_path: str) -> float:
    """
    Synthesizes a script into an audio file using a neural TTS priority chain.

    Each line of the cleaned script is treated as a breath-phrase; they are joined
    into one continuous text block and sent to the TTS engine, which handles
    prosody internally based on punctuation (commas, periods, em dashes).

    Priority chain:
      Tier 1 → edge-tts en-IN-NeerjaNeural  (Indian English, neural, free)
      Tier 2 → edge-tts en-IN-PrabhatNeural (Indian English, neural, fallback)
      Tier 3 → Kokoro am_michael            (local ONNX, zero internet required)

    Args:
        script: Cleaned script from script_cleaner.py (one phrase per line).
        output_path: Path to save the final audio file (.wav expected by pipeline).

    Returns:
        float: Total audio duration in seconds.

    Raises:
        RuntimeError: If all TTS engines fail, or the output file has zero duration.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Flatten multi-line script into one continuous text block.
    # Punctuation (commas, periods) from script_cleaner.py preserves prosody via TTS.
    clean_text = " ".join(line.strip() for line in script.splitlines() if line.strip())
    if not clean_text:
        raise ValueError("Script contains no text to synthesise.")

    logger.info(f"[TTS] Synthesising {len(clean_text.split())} words of voiceover text...")

    success = False

    # ── Tier 1: Primary Indian female voice ──────────────────────────────────
    logger.info(f"[TTS] Trying edge-tts ({_VOICE_PRIMARY})...")
    success = asyncio.run(
        _edge_tts_synthesize(clean_text, output_path, _VOICE_PRIMARY, _VOICE_RATE)
    )

    # ── Tier 2: Secondary Indian male voice ──────────────────────────────────
    if not success:
        logger.warning(f"[TTS] {_VOICE_PRIMARY} failed. Trying {_VOICE_SECONDARY}...")
        success = asyncio.run(
            _edge_tts_synthesize(clean_text, output_path, _VOICE_SECONDARY, _VOICE_RATE)
        )

    # ── Tier 3: Local Kokoro ONNX fallback ───────────────────────────────────
    if not success:
        logger.warning("[TTS] Both edge-tts voices failed. Trying Kokoro local fallback...")
        success = _kokoro_synthesize(clean_text, output_path)

    if not success:
        raise RuntimeError(
            "[TTS] All TTS engines failed.\n"
            "  → Check internet connection for edge-tts.\n"
            "  → Run: pip install edge-tts\n"
            "  → Install Kokoro for an offline fallback."
        )

    duration = _probe_duration(output_path)
    if duration <= 0:
        raise RuntimeError(
            f"[TTS] Audio file was created but has zero duration: {output_path}\n"
            "  → The ffmpeg wav conversion may have failed silently."
        )

    logger.info(f"[TTS] Voiceover saved to: {output_path} | Total duration: {duration:.2f}s")
    return duration

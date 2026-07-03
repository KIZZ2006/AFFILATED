"""
voiceover.py — Natural-sounding TTS synthesis using Supertonic 3 ONNX.

Key design decisions for human-sounding output:
  - The script is already split into breath-phrases by script_cleaner.py.
    Each line represents one natural spoken phrase.
  - Inter-phrase silence is VARIABLE: comma-connected phrases get a short
    breath (200ms), full-stop phrases get a natural pause (400ms), and
    any line that originally came from an ellipsis gets a dramatic pause (700ms).
  - Lines ending with a comma get a slightly shorter trailing silence to
    maintain connected prosody across a run-on thought.
  - We detect pause "weight" by the terminal character on each line.
"""

import os
import logging
import numpy as np
from supertonic import TTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pause duration map (seconds) — keyed on the terminal character of each phrase
# ---------------------------------------------------------------------------

_PAUSE_MAP = {
    ",": 0.18,   # Comma-breath: connected thought, barely a pause
    ".": 0.40,   # Full stop: natural end-of-sentence breath
    "!": 0.42,   # Exclamation: similar to full stop
    "?": 0.42,   # Question: slight upward intonation handled by TTS, then pause
    "_": 0.70,   # Ellipsis marker (we replace ... with trailing _ internally): dramatic pause
}
_DEFAULT_PAUSE = 0.40


def _classify_pause(phrase: str) -> float:
    """Return the appropriate post-phrase silence duration based on terminal punctuation."""
    if not phrase:
        return _DEFAULT_PAUSE
    return _PAUSE_MAP.get(phrase[-1], _DEFAULT_PAUSE)


def _preprocess_phrase(phrase: str) -> tuple[str, float]:
    """
    Strip any internal TTS-unfriendly characters and return
    (clean_text_for_tts, pause_seconds_after_this_phrase).

    Em dashes remaining in a phrase are converted to commas for smoother TTS.
    Ellipsis (if any survived cleaner) are converted to a period + long pause marker.
    """
    # Replace any surviving ellipsis with period (TTS will pause from the period)
    if "..." in phrase:
        phrase = phrase.replace("...", ".")
        clean = phrase.rstrip()
        return clean, _PAUSE_MAP["_"]   # Use the dramatic-pause duration

    # Em dashes → comma for smoother TTS delivery
    phrase = phrase.replace(" — ", ", ").replace("—", ", ")

    return phrase, _classify_pause(phrase)


def generate_voiceover(script: str, output_path: str) -> float:
    """
    Synthesizes a script into a single WAV audio file using Supertonic 3 ONNX TTS.

    Each line in the script is treated as a distinct breath-phrase and synthesised
    separately, with variable-length silence inserted between phrases to produce
    natural-sounding speech cadence.

    Args:
        script: Cleaned script from script_cleaner.py — one phrase per line.
        output_path: Path to save the final WAV file.

    Returns:
        float: Total audio duration in seconds.
    """
    logger.info("Initializing Supertonic TTS system...")
    tts = TTS(auto_download=True)

    # Voice style — M1 = male, warm tone. Can be overridden via config.
    voice_profile = "M1"
    logger.info(f"Voice profile: '{voice_profile}'")
    style = tts.get_voice_style(voice_name=voice_profile)

    # Build phrase list from lines, skip blank lines
    raw_lines = [line.strip() for line in script.splitlines() if line.strip()]
    if not raw_lines:
        raise ValueError("Script contains no text to synthesise.")

    sample_rate = getattr(tts, "sample_rate", 44100)
    logger.info(f"Sampling rate: {sample_rate} Hz | Phrases: {len(raw_lines)}")

    wav_segments: list[np.ndarray] = []
    accumulated_duration = 0.0

    for idx, raw_line in enumerate(raw_lines):
        phrase, pause_sec = _preprocess_phrase(raw_line)
        is_last = idx == len(raw_lines) - 1

        logger.info(
            f"[TTS] Phrase {idx + 1}/{len(raw_lines)} "
            f"({len(phrase.split())} words, {pause_sec:.2f}s pause after): '{phrase}'"
        )

        try:
            wav, duration = tts.synthesize(phrase, voice_style=style, lang="en")

            # Ensure the output is a 1D numpy array for easy operations
            if not isinstance(wav, np.ndarray):
                wav = np.array(wav, dtype=np.float32).flatten()
            else:
                wav = wav.astype(np.float32).flatten()

            wav_segments.append(wav)
            accumulated_duration += float(duration)

            # Add inter-phrase silence (skip after the very last phrase)
            if not is_last:
                pause_samples = int(sample_rate * pause_sec)
                silence = np.zeros(pause_samples, dtype=np.float32)
                wav_segments.append(silence)
                accumulated_duration += pause_sec

        except Exception as e:
            logger.error(f"[TTS] Failed to synthesise phrase '{phrase}': {e}")
            raise

    if not wav_segments:
        raise RuntimeError("No audio segments were generated.")

    logger.info("Concatenating waveforms...")
    combined_wav = np.concatenate(wav_segments)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tts.save_audio(combined_wav, output_path)

    logger.info(f"Voiceover saved to: {output_path} | Total duration: {accumulated_duration:.2f}s")
    return accumulated_duration

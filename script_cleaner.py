"""
script_cleaner.py — Script segmentation optimised for natural TTS delivery.

Philosophy:
  The number one cause of robotic-sounding TTS is feeding it short, choppy,
  disconnected sentence fragments. Real speech has longer breath-phrases joined
  by natural rhythm — "It's USB rechargeable... and it fits in your cup holder"
  sounds human; "It's USB rechargeable." / "It fits in your cup holder." does not.

  This module therefore does the OPPOSITE of aggressive sentence splitting:
    1. Preserves the natural flow the LLM wrote.
    2. Splits ONLY on explicit pause markers the prompt asked for (... and —)
       and on full terminal punctuation (. ! ?) — but only when the resulting
       phrase is long enough to stand alone (≥ 5 words).
    3. Merges any tiny fragments back into the previous phrase.
    4. Strips only obvious LLM filler openers that sound unnatural.
    5. Ensures each line ends with punctuation so TTS intonation falls correctly.

Output:
  One breath-phrase per line. Each line should take 3-8 seconds to say aloud.
  Ready for Supertonic TTS line-by-line synthesis with variable inter-phrase pauses.
"""

import re
import logging
import subprocess
import sys

try:
    import nltk
    for _pkg, _folder in [("punkt", "tokenizers"), ("punkt_tab", "tokenizers"), ("stopwords", "corpora")]:
        try:
            nltk.data.find(f"{_folder}/{_pkg}")
        except Exception:
            try:
                nltk.download(_pkg, quiet=True)
            except Exception:
                pass
except Exception as _e:
    logger.warning(f"NLTK optional import warning: {_e}")
    nltk = None


# ---------------------------------------------------------------------------
# spaCy loader (kept for NER / future use — not used for sentence splitting)
# ---------------------------------------------------------------------------

def load_spacy_nlp():
    """
    Loads the 'en_core_web_sm' spaCy model safely.
    Tries direct package import first, then spacy.load fallback.
    """
    try:
        import en_core_web_sm
        return en_core_web_sm.load()
    except Exception:
        try:
            return spacy.load("en_core_web_sm")
        except Exception as e:
            logger.warning(f"spaCy model 'en_core_web_sm' not available: {e}. Continuing with fallback cleaning.")
            return None


# ---------------------------------------------------------------------------
# Filler opener list
# ---------------------------------------------------------------------------

FILLER_OPENERS = [
    "i just found",
    "in today's video",
    "let's dive in",
    "are you tired of",
    "look no further",
    "hey guys",
    "welcome back",
    "in this video",
    "today we are talking about",
    "have you ever wondered",
    "so today i want to talk about",
]


# ---------------------------------------------------------------------------
# Core splitting logic — phrase-aware, not word-chopping
# ---------------------------------------------------------------------------

# Pause markers the prompt instructs the LLM to use
_ELLIPSIS_RE = re.compile(r'\s*\.\.\.\s*')
_EMDASH_RE   = re.compile(r'\s*—\s*')
# Terminal sentence end followed by a capital letter or end of string
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z\'"])')


def _split_into_phrases(text: str) -> list[str]:
    """
    Split text into breath-phrases honouring pause markers.

    Priority order:
      1. Ellipsis   (...) → hard split, long pause in voiceover
      2. Em dash    (—)   → hard split, emphasis pause in voiceover
      3. Sentence end (. ! ?) followed by capital → split only if both halves ≥ 5 words

    Returns a list of phrase strings, cleaned of leading/trailing whitespace.
    """
    # Step 1: split on ellipsis
    parts_after_ellipsis: list[str] = []
    for segment in _ELLIPSIS_RE.split(text):
        seg = segment.strip()
        if seg:
            parts_after_ellipsis.append(seg)

    # Step 2: split each segment on em dashes
    parts_after_emdash: list[str] = []
    for segment in parts_after_ellipsis:
        for sub in _EMDASH_RE.split(segment):
            sub = sub.strip()
            if sub:
                parts_after_emdash.append(sub)

    # Step 3: within each part, split on terminal punctuation only when
    #         both resulting halves are long enough to be meaningful phrases
    final_phrases: list[str] = []
    for segment in parts_after_emdash:
        terminal_splits = _SENTENCE_END_RE.split(segment)
        for phrase in terminal_splits:
            phrase = phrase.strip()
            if phrase:
                final_phrases.append(phrase)

    return final_phrases


def _strip_filler(phrase: str) -> str | None:
    """
    Strip known LLM filler openers. Returns None if nothing useful remains.
    """
    lower = phrase.lower()
    for filler in FILLER_OPENERS:
        if lower.startswith(filler):
            remainder = phrase[len(filler):].strip().lstrip(",:;- ")
            if len(remainder.split()) > 3:
                return remainder[0].upper() + remainder[1:]
            else:
                return None   # Fragment too small — drop it
    return phrase


def _ensure_punctuation(phrase: str) -> str:
    """Add a period if the phrase has no terminal punctuation."""
    phrase = phrase.rstrip()
    if phrase and phrase[-1] not in ".!?,":
        phrase += "."
    return phrase


def _merge_short_fragments(phrases: list[str], min_words: int = 5) -> list[str]:
    """
    Merge phrases shorter than min_words into the previous phrase.
    Prevents TTS from reading isolated short bursts with wrong intonation.
    """
    if not phrases:
        return phrases
    merged: list[str] = [phrases[0]]
    for phrase in phrases[1:]:
        if len(phrase.split()) < min_words and merged:
            # Join without extra period if prev already ends with punct
            prev = merged[-1].rstrip(".!?,")
            # Use a comma-connect for smooth prosody instead of a hard period
            merged[-1] = prev + ", " + phrase[0].lower() + phrase[1:]
        else:
            merged.append(phrase)
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_script(raw_script: str) -> str:
    """
    Cleans and segments the raw ad script for natural-sounding TTS delivery.

    Steps:
      1. Normalise whitespace and remove internal line breaks.
      2. Strip filler openers that sound unnatural in a voiceover.
      3. Split into breath-phrases at ellipsis, em-dash, then sentence boundaries.
      4. Merge short fragments (< 5 words) back into previous phrase.
      5. Ensure each line ends with punctuation for correct TTS intonation.

    Args:
        raw_script: The raw output from the script generator.

    Returns:
        str: One breath-phrase per line. Ideal for line-by-line TTS synthesis.
    """
    # Normalise
    text = " ".join(raw_script.split())

    # Strip filler opener on the full text (before splitting)
    cleaned = _strip_filler(text)
    if cleaned is None:
        # Whole first sentence was a filler — try to strip just the opener phrase
        # by splitting at the first period and retrying the rest
        first_period = text.find(".")
        if first_period != -1:
            text = text[first_period + 1:].strip()
        cleaned = text
    else:
        text = cleaned

    # Split into breath-phrases
    phrases = _split_into_phrases(text)
    logger.info(f"[cleaner] Split into {len(phrases)} phrase(s).")

    # Strip filler on each phrase individually (in case opener is mid-sentence)
    filtered: list[str] = []
    for phrase in phrases:
        result = _strip_filler(phrase)
        if result:
            filtered.append(result)

    if not filtered:
        logger.warning("[cleaner] All phrases were filtered — returning raw script unchanged.")
        return raw_script.strip()

    # Merge short fragments
    filtered = _merge_short_fragments(filtered, min_words=5)

    # Ensure punctuation on every line
    final = [_ensure_punctuation(p) for p in filtered]

    # Log stats
    total_words = sum(len(p.split()) for p in final)
    logger.info(
        f"[cleaner] Output: {len(final)} line(s), ~{total_words} words. "
        f"Avg phrase length: {total_words // max(len(final), 1)} words/line."
    )
    for i, line in enumerate(final, 1):
        logger.debug(f"[cleaner] Line {i} ({len(line.split())}w): {line}")

    return "\n".join(final)

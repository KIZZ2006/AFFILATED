"""
script_generator.py  —  Multi-provider script generation with fallback chain.

Provider order (tried in sequence, falls through on any error):
  1. Groq  —  llama-3.3-70b-versatile
     Identical model family to NIM primary, but served through Groq's
     ultra-fast inference (typically < 1 s TTFT). Set as primary to bypass NIM rate limits.

  2. NVIDIA NIM  — meta/llama-3.3-70b-instruct
     Best instruction-following quality on the NIM catalogue, used as high-quality fallback.

  3. Google Gemini  —  gemini-1.5-flash
     Fast multimodal model; reliable fallback with a generous free tier.

Each provider uses the same carefully engineered prompt.  The prompt is
crafted for direct-response copywriting: hook → benefits → price reveal →
CTA, ≤ 70 words, no markdown, no stage directions.
"""

import logging
import time

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared prompt builder
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are an expert short-form video ad scriptwriter who writes scripts specifically "
    "for text-to-speech voiceover. Your scripts sound like a real, enthusiastic person talking — "
    "not a list of features. You use natural speech rhythm, contractions, ellipses for dramatic "
    "pauses, and em dashes for emphasis beats. The output is ONLY the spoken words."
)

def _build_prompt(product_name: str, price: str, features: list[str], niche: str) -> str:
    feat_lines = "\n".join(f"- {f.strip()}" for f in features if f.strip())
    return f"""Write a spoken voiceover script for a 30-second vertical video ad.

Product : {product_name}
Price   : {price}
Niche   : {niche}
Features:
{feat_lines}

CRITICAL — This will be read aloud by a TTS engine. It MUST sound like a real person talking:

1. HOOK      — Open with ONE punchy statement that stops the scroll. Bold claim or surprising fact.
               Example style: "This little thing changed my entire morning routine."

2. FLOW      — Use natural spoken transitions: "and honestly...", "what I love is...",
               "oh — and...", "plus...", "but here's the thing —".
               Write the way you'd tell a friend, not a bullet-point list.

3. PAUSES    — Use ellipsis (...) for a dramatic 1-second pause.
               Use an em dash ( — ) for a quick emphasis beat.
               Example: "It blends ice in seconds... and you can take it anywhere."

4. PRICE     — Reveal the price ({price}) as a value moment, like:
               "All of this — for just {price}." or "And it's only {price}."
               ALWAYS include the exact price string including currency symbol.

5. CTA       — End with EXACTLY this sentence: "Comment 'link' or click the link in my bio to get yours."

6. LENGTH    — 65-80 words total. NOT shorter. The rhythm must feel complete, not rushed.

7. FORMAT    — Plain text only. No bullet points, no asterisks, no markdown, no labels like
               'Hook:' or 'CTA:', no stage directions, no quotes around the script itself.
               Contractions are REQUIRED (it's, you'll, I'll, that's, can't, won't, etc.)"""


def _clean(text: str) -> str:
    """Strip residual markdown and leading/trailing whitespace. Log word count."""
    for ch in ("`", "*", "_", "#"):
        text = text.replace(ch, "")
    # Remove common preamble patterns models insert despite instructions
    for prefix in (
        "Here is your script:",
        "Here's the script:",
        "Script:",
        "Ad script:",
        "Here is the script:",
    ):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].lstrip()
    text = text.strip()
    wc = len(text.split())
    logger.info(f"[script] Cleaned script word count: {wc}")
    if wc < 55:
        logger.warning(f"[script] Word count {wc} is below the 60-word target — consider re-running.")
    return text


# ---------------------------------------------------------------------------
# Provider 1 — NVIDIA NIM
# ---------------------------------------------------------------------------

def _try_nim(prompt: str) -> str:
    """Call NIM API with exponential backoff. Returns cleaned script text."""
    from openai import OpenAI

    if not config.NIM_API_KEY:
        raise ValueError("NIM_API_KEY not configured.")

    client = OpenAI(base_url=config.NIM_BASE_URL, api_key=config.NIM_API_KEY)
    model  = config.NIM_MODEL   # default: meta/llama-3.3-70b-instruct

    for attempt in range(4):
        try:
            logger.info(f"[NIM] Attempt {attempt + 1}/4 — model: {model}")
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.72,
                top_p=0.95,
                max_tokens=160,
                timeout=30,
            )
            text = resp.choices[0].message.content
            if text and text.strip():
                return _clean(text)
        except Exception as e:
            if attempt == 3:
                raise
            delay = 2.0 * (2 ** attempt)
            logger.warning(f"[NIM] Error: {e}. Retrying in {delay:.0f}s...")
            time.sleep(delay)

    raise RuntimeError("[NIM] All retries exhausted.")


# ---------------------------------------------------------------------------
# Provider 2 — Groq  (llama-3.3-70b-versatile — fastest inference available)
# ---------------------------------------------------------------------------

def _try_groq(prompt: str) -> str:
    """Call Groq API. Returns cleaned script text."""
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError("groq package not installed. Run: pip install groq")

    if not config.GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY not configured.")

    client = Groq(api_key=config.GROQ_API_KEY)
    model  = "llama-3.3-70b-versatile"   # fastest Llama-70B on Groq

    logger.info(f"[Groq] Calling model: {model}")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.72,
        top_p=0.95,
        max_tokens=160,
        timeout=15,
    )
    text = resp.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError("[Groq] Empty response.")
    return _clean(text)


# ---------------------------------------------------------------------------
# Provider 3 — Google Gemini  (gemini-1.5-flash)
# ---------------------------------------------------------------------------

def _try_gemini(prompt: str) -> str:
    """Call Gemini API via google-generativeai. Returns cleaned script text."""
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError(
            "google-generativeai package not installed. "
            "Run: pip install google-generativeai"
        )

    if not config.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured.")

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=_SYSTEM,
        generation_config=genai.GenerationConfig(
            temperature=0.72,
            top_p=0.95,
            max_output_tokens=160,
        ),
    )

    logger.info("[Gemini] Calling gemini-1.5-flash...")
    resp = model.generate_content(prompt)
    text = resp.text
    if not text or not text.strip():
        raise RuntimeError("[Gemini] Empty response.")
    return _clean(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_script(
    product_name: str,
    price: str,
    features: list[str],
    niche: str,
) -> str:
    """
    Generate a 55-70 word affiliate video ad script.

    Tries providers in order: NIM → Groq → Gemini.
    Returns the first successful, non-empty result.

    Args:
        product_name: Name of the affiliate product.
        price:        Price string (e.g. "$24.99").
        features:     List of key product features.
        niche:        Target niche (e.g. "kitchen gadgets").

    Returns:
        Cleaned, plain-text spoken script.

    Raises:
        RuntimeError: If all three providers fail.
    """
    prompt = _build_prompt(product_name, price, features, niche)

    providers = [
        ("Groq (llama-3.3-70b)",  _try_groq),
        ("NIM (llama-3.3-70b)",   _try_nim),
        ("Gemini (1.5-flash)",    _try_gemini),
    ]

    last_error: Exception | None = None

    for label, fn in providers:
        try:
            logger.info(f"[script_generator] Trying provider: {label}")
            script = fn(prompt)
            if script:
                logger.info(
                    f"[script_generator] SUCCESS via {label} "
                    f"({len(script.split())} words)"
                )
                return script
        except Exception as e:
            logger.warning(f"[script_generator] {label} failed: {e}")
            last_error = e

    raise RuntimeError(
        f"All script generation providers failed. Last error: {last_error}"
    )

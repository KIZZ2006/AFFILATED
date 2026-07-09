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
from script_history import is_hook_duplicate, save_script

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared prompt builder
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are an expert short-form video ad scriptwriter creating scripts for Amazon India affiliate marketing. "
    "Your audience is Indian buyers on Instagram Reels and YouTube Shorts. "
    "Write scripts that sound like a real, enthusiastic Indian content creator talking — "
    "not a list of features. Use natural Indian English with relatable phrases like "
    "'yaar', 'dosto', 'sach mein', 'best deal on Amazon', 'bilkul worth it'. "
    "Use natural speech rhythm, contractions, ellipses for dramatic pauses, and em dashes for emphasis. "
    "Always reference prices in Rupees. The output is ONLY the spoken words."
)

def price_to_spoken_words(price_str: str) -> str:
    """
    Converts any price string to natural spoken words.
    Auto-detects currency symbol — ₹ produces Rupees, $ produces Dollars.
    Supports Indian numbering: lakh (1,00,000) and thousand.

    Examples:
        '₹1,299'  → 'one thousand two hundred and ninety nine rupees'
        '₹84,999' → 'eighty four thousand nine hundred and ninety nine rupees'
        '$49.99'  → 'forty nine dollars and ninety nine cents'
    """
    import re
    is_rupee = "₹" in price_str
    cleaned = price_str.replace("₹", "").replace("$", "").replace(",", "").strip()
    match = re.match(r'^(\d+)(?:\.(\d{1,2}))?$', cleaned)
    if not match:
        return price_str

    val_int = int(match.group(1))
    cents_str = match.group(2)

    units = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
             "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens_w = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    def num_words(n: int) -> str:
        if n < 20:
            return units[n]
        if n < 100:
            t, r = divmod(n, 10)
            return f"{tens_w[t]} {units[r]}" if r else tens_w[t]
        if n < 1_000:
            h, r = divmod(n, 100)
            return f"{units[h]} hundred" + (f" and {num_words(r)}" if r else "")
        if n < 1_00_000:  # up to 99,999 — thousand system
            k, r = divmod(n, 1_000)
            return f"{num_words(k)} thousand" + (f" {num_words(r)}" if r else "")
        if n < 1_00_00_000:  # up to 9,99,99,999 — lakh system
            l, r = divmod(n, 1_00_000)
            return f"{num_words(l)} lakh" + (f" {num_words(r)}" if r else "")
        return str(n)

    if is_rupee:
        return f"{num_words(val_int)} rupees"
    else:
        d_text = num_words(val_int)
        if cents_str:
            cents_int = int(cents_str.ljust(2, "0"))
            if cents_int > 0:
                return f"{d_text} dollars and {num_words(cents_int)} cents"
        return f"{d_text} dollars"


def _build_prompt(product_name: str, price: str, features: list[str], niche: str) -> str:
    feat_lines = "\n".join(f"- {f.strip()}" for f in features if f.strip())
    spoken_price = price_to_spoken_words(price)
    return f"""Write a spoken voiceover script for a 30-second vertical video ad.

Product : {product_name}
Price   : {spoken_price} (numerical price display: {price})
Niche   : {niche}
Features:
{feat_lines}

CRITICAL — This will be read aloud by a TTS engine. It MUST sound like a real person talking:

1. HOOK      — Open with ONE extremely punchy statement that stops the scroll (under 12 words). Avoid 'are you tired of...' or standard intros. Use a pattern interrupt:
               * A warning/mistake hook: 'Stop buying normal [products/niche] until you see this...'
               * A curiosity loop: 'Amazon India has a secret [niche] hack that costs less than...'
               * A secret hook: 'I found the one thing you need to fix your [daily problem]...'
               Example style: "This Rs [price] Amazon find is secretly saving me hours of work..."

2. FLOW      — Use natural spoken transitions: "and honestly...", "what I love is...",
               "oh — and...", "plus...", "but here's the thing —".
               Write the way you'd tell a friend, not a marketing list.

3. PAUSES    — Use ellipsis (...) for a dramatic 1-second pause.
               Use an em dash ( — ) for a quick emphasis beat.
               Example: "It blends ice in seconds... and you can take it anywhere."

4. PRICE     — Reveal the price in natural spoken words format (e.g. "{spoken_price}" or "forty nine ninety nine").
               CRITICAL: NEVER write price symbols or digits like "$49.99" or "$18". ALWAYS write out spoken words!

5. CTA       — End with a line combining both: encourage the viewer to follow the account to get the product link sent directly to their DMs, and mention that the link is also in the bio as a fallback. (e.g., "Drop a follow to get the link sent straight to your DMs, or check the link in my bio!")

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
    
    candidate_models = ["gemini-1.5-flash-latest", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-pro"]
    last_err = None

    for m_name in candidate_models:
        try:
            logger.info(f"[Gemini] Trying model {m_name}...")
            model = genai.GenerativeModel(
                model_name=m_name,
                system_instruction=_SYSTEM,
                generation_config=genai.GenerationConfig(
                    temperature=0.72,
                    top_p=0.95,
                    max_output_tokens=160,
                ),
            )
            resp = model.generate_content(prompt)
            text = resp.text
            if text and text.strip():
                return _clean(text)
        except Exception as e:
            logger.warning(f"[Gemini] Model {m_name} failed: {e}")
            last_err = e

    raise RuntimeError(f"[Gemini] All model candidates failed. Last error: {last_err}")


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
    Checks script_history database for hook deduplication.
    """
    prompt = _build_prompt(product_name, price, features, niche)

    providers = [
        ("Groq (llama-3.3-70b)",  _try_groq),
        ("NIM (llama-3.3-70b)",   _try_nim),
        ("Gemini (1.5-flash)",    _try_gemini),
    ]

    def _execute_generation(p_text: str) -> str:
        last_error = None
        for label, fn in providers:
            try:
                logger.info(f"[script_generator] Trying provider: {label}")
                script = fn(p_text)
                if script:
                    logger.info(
                        f"[script_generator] SUCCESS via {label} "
                        f"({len(script.split())} words)"
                    )
                    return script
            except Exception as e:
                logger.warning(f"[script_generator] {label} failed: {e}")
                last_error = e
        raise RuntimeError(f"All script generation providers failed. Last error: {last_error}")

    # Generate initial script
    script = _execute_generation(prompt)

    # Hook deduplication check
    hook_line = script.split("\n")[0].split(".")[0].strip()
    is_dup, overlap, matched = is_hook_duplicate(hook_line)

    if is_dup:
        logger.warning(f"[script_generator] Hook '{hook_line}' matches existing hook '{matched}' ({overlap:.2f}). Regenerating once...")
        retry_prompt = prompt + "\n\nCRITICAL NOTE: A previous script used a very similar hook. Write a COMPLETELY DIFFERENT hook angle and opening line!"
        try:
            script = _execute_generation(retry_prompt)
            hook_line = script.split("\n")[0].split(".")[0].strip()
        except Exception as e:
            logger.warning(f"[script_generator] Retry generation failed, keeping first script: {e}")

    # Save final script to SQLite database
    save_script(hook_line, script, niche, product_name)
    return script


import os
import logging
import stable_whisper
import config

logger = logging.getLogger(__name__)

def custom_group_words(result, product_name=""):
    """
    Custom word grouping to keep natural phrase units together.
    Avoids splitting numbers and product names.
    """
    words = result.all_words()
    if not words:
        return result
        
    number_words = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
                    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
                    "hundred", "thousand", "lakh", "crore",          # Indian number system
                    "rupees", "rupee",                               # INR currency words
                    "dollars", "cents", "dollar", "cent", "and"}    # USD kept for backward compat
                    
    prod_words = set(product_name.lower().split())
    
    new_segments = []
    current_words = []
    
    def is_number(w): return w.word.strip().lower() in number_words
    def is_prod(w): return w.word.strip().lower() in prod_words

    for i, w in enumerate(words):
        current_words.append(w)
        if len(current_words) >= 2:
            next_w = words[i+1] if i + 1 < len(words) else None
            should_split = True
            if next_w:
                if is_number(w) and is_number(next_w): should_split = False
                elif is_prod(w) and is_prod(next_w): should_split = False
                
                if len(current_words) >= 5: should_split = True

            if should_split:
                seg_dict = dict(
                    id=len(new_segments), seek=0, start=current_words[0].start, end=current_words[-1].end,
                    text="".join([x.word for x in current_words]), tokens=[], temperature=0.0, avg_logprob=0.0,
                    compression_ratio=0.0, no_speech_prob=0.0,
                    words=[dict(word=x.word, start=x.start, end=x.end, probability=getattr(x, "probability", 1.0)) for x in current_words]
                )
                new_segments.append(seg_dict)
                current_words = []
                
    if current_words:
        seg_dict = dict(
            id=len(new_segments), seek=0, start=current_words[0].start, end=current_words[-1].end,
            text="".join([x.word for x in current_words]), tokens=[], temperature=0.0, avg_logprob=0.0,
            compression_ratio=0.0, no_speech_prob=0.0,
            words=[dict(word=x.word, start=x.start, end=x.end, probability=getattr(x, "probability", 1.0)) for x in current_words]
        )
        new_segments.append(seg_dict)
        
    import stable_whisper
    return stable_whisper.result.WhisperResult(new_segments)


def generate_captions(voiceover_path: str, output_ass_path: str, product_name: str = "") -> list[dict]:
    """
    Transcribes the synthesized voiceover file using stable-ts (Whisper).
    Extracts word-level timestamps and writes them into a highly readable, 
    custom-styled .ass subtitle file designed for vertical (1080x1920) videos.
    
    Returns:
        list[dict]: List of word timestamp dicts [{'word': str, 'start': float, 'end': float}]
    """
    if not os.path.exists(voiceover_path):
        raise FileNotFoundError(f"Voiceover source file not found at: {voiceover_path}")

    # Load the Whisper model specified in environment or default to 'base'
    whisper_model_name = os.getenv("WHISPER_MODEL", "base")
    logger.info(f"Loading stable-whisper model: '{whisper_model_name}'...")
    model = stable_whisper.load_model(whisper_model_name)
    
    logger.info(f"Transcribing audio file '{voiceover_path}'...")
    result = model.transcribe(voiceover_path)
    
    # Extract word-level timestamps for visual timing synchronization
    word_timestamps = []
    try:
        if hasattr(result, "words") and result.words:
            word_timestamps = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)} for w in result.words]
        elif hasattr(result, "segments"):
            for seg in result.segments:
                for w in getattr(seg, "words", []):
                    word_timestamps.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    except Exception as e:
        logger.warning(f"[captioner] Failed to extract word timestamps: {e}")
    
    # Ensure destination directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)
    
    logger.info(f"Exporting intelligently grouped ASS subtitles to '{output_ass_path}'...")

    # Apply custom word grouping
    result = custom_group_words(result, product_name)

    # 6-8% of 1920 is ~115-153. Using 130 for clean visibility.
    target_font_size = 130 

    result.to_ass(
        output_ass_path,
        font=config.CAPTION_FONT,
        font_size=target_font_size,
        PrimaryColour='&H00ffffff',  # Clean white fill
        OutlineColour='&H00000000',  # Dark outline
        Outline=6,                   # Adjusted for 1920p resolution
        Shadow=3,                    # Adjusted for 1920p resolution
        Alignment=2,  # 2 = Bottom Center
        MarginV=280,  # Vertical margin to stay clear of IG overlay and video center
        Bold=1
    )
    
    # Fix the PlayRes values to match standard 1080x1920
    # stable-ts defaults to 384x288, which throws off all font size and margin calculations.
    if os.path.exists(output_ass_path):
        with open(output_ass_path, "r", encoding="utf-8") as f:
            ass_text = f.read()
        ass_text = ass_text.replace("PlayResX: 384", "PlayResX: 1080").replace("PlayResY: 288", "PlayResY: 1920")
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_text)
    
    logger.info("Subtitle file generation completed successfully.")
    return word_timestamps


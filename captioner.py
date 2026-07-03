import os
import logging
import stable_whisper
import config

logger = logging.getLogger(__name__)

def generate_captions(voiceover_path: str, output_ass_path: str) -> None:
    """
    Transcribes the synthesized voiceover file using stable-ts (Whisper).
    Extracts word-level timestamps and writes them into a highly readable, 
    custom-styled .ass subtitle file designed for vertical (1080x1920) videos.
    
    Args:
        voiceover_path (str): Path to the input voiceover WAV/audio file.
        output_ass_path (str): Path to save the resulting ASS subtitle file.
    """
    if not os.path.exists(voiceover_path):
        raise FileNotFoundError(f"Voiceover source file not found at: {voiceover_path}")

    # Load the Whisper model specified in environment or default to 'base'
    whisper_model_name = os.getenv("WHISPER_MODEL", "base")
    logger.info(f"Loading stable-whisper model: '{whisper_model_name}'...")
    model = stable_whisper.load_model(whisper_model_name)
    
    logger.info(f"Transcribing audio file '{voiceover_path}'...")
    result = model.transcribe(voiceover_path)
    
    # Ensure destination directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)
    
    logger.info(f"Exporting stylized ASS subtitles to '{output_ass_path}'...")
    
    # We pass the custom style variables from config.py to result.to_ass.
    # The parameters are mapped to corresponding ASS style definitions:
    # - font: Font name (e.g., Arial, Inter)
    # - font_size: Font size (suitable for vertical 1080x1920)
    # - PrimaryColour: Text color in BGR format (e.g. &H00ffffff for white)
    # - OutlineColour: Stroke border color (e.g. &H00000000 for black)
    # - Outline: Width of the outline stroke
    # - Shadow: Shadow depth offset
    # - Alignment: 2 = Bottom-center (standard subtitle position)
    # - MarginV: Vertical margin from the bottom edge (raised to bottom-third)
    # - Bold: Enable bold typography
    result.to_ass(
        output_ass_path,
        font=config.CAPTION_FONT,
        font_size=config.CAPTION_FONT_SIZE,
        PrimaryColour=config.CAPTION_PRIMARY_COLOR,
        OutlineColour=config.CAPTION_OUTLINE_COLOR,
        Outline=config.CAPTION_OUTLINE_WIDTH,
        Shadow=config.CAPTION_SHADOW,
        Alignment=config.CAPTION_ALIGNMENT,
        MarginV=config.CAPTION_MARGIN_V,
        Bold=1
    )
    
    logger.info("Subtitle file generation completed successfully.")

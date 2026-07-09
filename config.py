import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# NVIDIA NIM — primary script generator
# meta/llama-3.3-70b-instruct: best instruction-following model in the NIM
# catalogue that responded within timeout during live evaluation.
NIM_API_KEY = os.getenv("NIM_API_KEY", "")
NIM_MODEL   = os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct")
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")

PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Groq — fallback 1: fastest inference, same Llama-3.3-70B family
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Google Gemini — fallback 2: generous quota, reliable
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# YouTube API Credentials and Token Paths (unused — manual upload workflow)
# YT_CLIENT_SECRET_PATH = os.getenv("YT_CLIENT_SECRET_PATH", "client_secret.json")
# YT_TOKEN_PATH = os.getenv("YT_TOKEN_PATH", "token.json")

# Public host / ngrok endpoint for storefront and webhook callbacks
PUBLIC_HOST_ENDPOINT = os.getenv("PUBLIC_HOST_ENDPOINT", "http://localhost:5000")

# Amazon Associates tag — read exclusively through amazon_affiliate.py via this config
AMAZON_ASSOCIATE_ID = os.getenv("AMAZON_ASSOCIATE_ID", "")
# Telegram Bot API Credentials & Auto-Publish Control
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
AUTO_PUBLISH_ENABLED = os.getenv("AUTO_PUBLISH_ENABLED", "false").lower() == "true"

# Video Assembly Tunables
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1080"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1920"))
TARGET_VOICEOVER_DURATION = float(os.getenv("TARGET_VOICEOVER_DURATION", "27.5"))  # ~25-30 seconds

# Subtitle / Caption Style Settings (for ASS Substation Alpha files)
CAPTION_FONT = os.getenv("CAPTION_FONT", "Arial")
CAPTION_FONT_SIZE = int(os.getenv("CAPTION_FONT_SIZE", "52"))
CAPTION_PRIMARY_COLOR = os.getenv("CAPTION_PRIMARY_COLOR", "&H00ffffff")  # White (BGR/ABGR hex format)
CAPTION_OUTLINE_COLOR = os.getenv("CAPTION_OUTLINE_COLOR", "&H00000000")  # Black
CAPTION_OUTLINE_WIDTH = int(os.getenv("CAPTION_OUTLINE_WIDTH", "4"))
CAPTION_SHADOW = int(os.getenv("CAPTION_SHADOW", "2"))
CAPTION_ALIGNMENT = int(os.getenv("CAPTION_ALIGNMENT", "5"))  # 5 is middle center
CAPTION_MARGIN_V = int(os.getenv("CAPTION_MARGIN_V", "0"))  # Centered vertically in middle third


def validate_config():
    """
    Validates that the essential API keys are provided.
    Returns a list of missing configuration fields.
    At least ONE of NIM / Groq / Gemini must be present for script generation.
    """
    missing = []
    if not PEXELS_API_KEY:
        missing.append("PEXELS_API_KEY")
    # Script generation: warn if ALL three providers are missing
    if not NIM_API_KEY and not GROQ_API_KEY and not GEMINI_API_KEY:
        missing.append("NIM_API_KEY / GROQ_API_KEY / GEMINI_API_KEY (need at least one)")
    return missing

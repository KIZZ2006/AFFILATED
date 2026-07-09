"""
publish_next.py — Processes queued videos according to AUTO_PUBLISH_ENABLED config.

Flow:
  1. Reads queue.json.
  2. If AUTO_PUBLISH_ENABLED is False (default):
     - Finds oldest entry with review_status == "pending".
     - Sends video message to Telegram chat ID with inline "✅ Posted" review button.
     - Updates review_status = "sent_to_telegram".
     - Social media uploaders remain intact and dormant until AUTO_PUBLISH_ENABLED is set to true.
  3. If AUTO_PUBLISH_ENABLED is True:
     - Finds oldest entry with review_status != "posted".
     - Publishes to Instagram Reels and YouTube Shorts.
     - Once published, updates review_status = "posted" and cleans up local video files.
"""

import json
import logging
import os
import shutil
import sys

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from telegram_notifier import send_video_to_telegram

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("publish_next")

QUEUE_FILE = "queue.json"


def cleanup_local_assets(video_id: str, video_path: str):
    """Deletes local MP4 and all temporary generation files from disk."""
    if video_path and os.path.exists(video_path):
        try:
            os.remove(video_path)
            logger.info(f"[publish_next] Deleted video file: {video_path}")
        except Exception as e:
            logger.warning(f"[publish_next] Failed to delete video file '{video_path}': {e}")

    output_dir = os.path.dirname(video_path) if video_path else "output"
    temp_files = [
        os.path.join(output_dir, f"raw_script_{video_id}.txt"),
        os.path.join(output_dir, f"cleaned_script_{video_id}.txt"),
        os.path.join(output_dir, f"voiceover_{video_id}.wav"),
        os.path.join(output_dir, f"captions_{video_id}.ass")
    ]
    for tf in temp_files:
        if os.path.exists(tf):
            try:
                os.remove(tf)
                logger.info(f"[publish_next] Deleted temporary file: {tf}")
            except Exception as e:
                logger.warning(f"[publish_next] Failed to delete temporary file '{tf}': {e}")

    visuals_dir = os.path.join(output_dir, f"visuals_{video_id}")
    if os.path.exists(visuals_dir):
        try:
            shutil.rmtree(visuals_dir)
            logger.info(f"[publish_next] Deleted temporary visuals directory: {visuals_dir}")
        except Exception as e:
            logger.warning(f"[publish_next] Failed to delete visuals directory '{visuals_dir}': {e}")


def publish_next():
    if not os.path.exists(QUEUE_FILE):
        logger.info("[publish_next] queue.json does not exist. Nothing to process.")
        return

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        logger.error(f"[publish_next] Error reading {QUEUE_FILE}: {e}")
        return

    if not queue:
        logger.info("[publish_next] Queue is empty. Nothing to process.")
        return

    # Auto-publish disabled (default): delivery via Telegram for human review loop
    if not config.AUTO_PUBLISH_ENABLED:
        logger.info("[publish_next] AUTO_PUBLISH_ENABLED is false. Telegram Review Loop active.")
        
        target_entry = None
        target_idx = -1
        for idx, entry in enumerate(queue):
            # Normalise old fields if present
            status = entry.get("review_status")
            if not status:
                status = "pending"
                entry["review_status"] = status
                
            if status == "pending":
                target_entry = entry
                target_idx = idx
                break

        if not target_entry:
            logger.info("[publish_next] No pending videos waiting for Telegram review.")
            return

        video_id = target_entry.get("video_id", "unknown")
        video_path = target_entry.get("video_path", "")
        product_name = target_entry.get("product_name", "Product")
        caption_text = target_entry.get("caption_text", "")

        logger.info(f"[publish_next] Delivering video '{video_id}' ({product_name}) to Telegram review queue...")
        sent = send_video_to_telegram(video_id, video_path, caption_text, product_name)
        if sent:
            target_entry["review_status"] = "sent_to_telegram"
            queue[target_idx] = target_entry
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=2)
            logger.info(f"[publish_next] Video '{video_id}' status updated to 'sent_to_telegram'.")
        else:
            logger.warning(f"[publish_next] Could not send video '{video_id}' to Telegram. Will retry on next run.")
        return

    # Auto-publish enabled: deliver via Telegram only (user uploads to Instagram manually)
    logger.info("[publish_next] AUTO_PUBLISH_ENABLED is true. Delivering to Telegram for manual upload.")
    target_entry = None
    target_idx = -1

    for idx, entry in enumerate(queue):
        if entry.get("review_status") == "pending":
            target_entry = entry
            target_idx = idx
            break

    if not target_entry:
        logger.info("[publish_next] No pending videos found in queue.")
        return

    video_id = target_entry.get("video_id", "unknown")
    video_path = target_entry.get("video_path", "")
    product_name = target_entry.get("product_name", "Product")
    caption_text = target_entry.get("caption_text", "")

    sent = send_video_to_telegram(video_id, video_path, caption_text, product_name)
    if sent:
        target_entry["review_status"] = "sent_to_telegram"
        logger.info(f"[publish_next] Video '{video_id}' sent to Telegram for manual review.")
    else:
        logger.warning(f"[publish_next] Telegram delivery failed for '{video_id}'. Will retry on next run.")

    queue[target_idx] = target_entry
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)


if __name__ == "__main__":
    publish_next()

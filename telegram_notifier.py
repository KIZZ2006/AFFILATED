"""
telegram_notifier.py — Telegram bot delivery and human review loop for generated videos.

Features:
  1. Sends finished video & caption to configured Telegram Chat ID with an inline "✅ Posted" button.
  2. Enforces a 45MB file size limit check before upload attempt.
  3. Includes a Flask blueprint / route for Telegram callback queries:
     - When "✅ Posted" is tapped, updates queue.json review_status to "posted"
       and automatically cleans up all local video files and temporary assets from disk.
"""

import json
import logging
import os
import shutil
import requests
from flask import Blueprint, request, jsonify
import config

logger = logging.getLogger(__name__)

telegram_bp = Blueprint("telegram_bp", __name__)
QUEUE_FILE = "queue.json"


def check_file_size_ok(video_path: str, max_mb: float = 45.0) -> bool:
    """Verifies video file size is under the specified limit (45MB)."""
    if not os.path.exists(video_path):
        return False
    size_bytes = os.path.getsize(video_path)
    size_mb = size_bytes / (1024 * 1024)
    if size_mb > max_mb:
        logger.warning(
            f"[telegram] Video file '{video_path}' size ({size_mb:.1f} MB) exceeds limit ({max_mb} MB). "
            f"Skipping Telegram upload."
        )
        return False
    return True


def send_video_to_telegram(video_id: str, video_path: str, caption_text: str, product_name: str) -> bool:
    """
    Sends the finished MP4 video message to Telegram with an inline "✅ Posted" review button.
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("[telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in config. Skipping notification.")
        return False

    if not os.path.exists(video_path):
        logger.error(f"[telegram] Video file not found: {video_path}")
        return False

    if not check_file_size_ok(video_path, max_mb=45.0):
        return False

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    
    reply_markup = {
        "inline_keyboard": [
            [{"text": "✅ Posted", "callback_data": f"posted:{video_id}"}]
        ]
    }

    try:
        logger.info(f"[telegram] Sending video '{video_id}' to Telegram chat '{chat_id}'...")
        short_caption = f"*YASNA — New Video Ready!*\n\n*Product:* {product_name}\n*Video ID:* {video_id}"

        with open(video_path, "rb") as video_file:
            payload = {
                "chat_id": chat_id,
                "caption": short_caption,
                "parse_mode": "Markdown",
                "reply_markup": json.dumps(reply_markup)
            }
            files = {"video": video_file}
            response = requests.post(url, data=payload, files=files, timeout=120)

        if response.status_code == 200:
            logger.info(f"[telegram] Video '{video_id}' successfully delivered to Telegram.")
            return True
        else:
            logger.error(f"[telegram] Telegram sendVideo API failed ({response.status_code}): {response.text}")
            return False

    except Exception as e:
        logger.error(f"[telegram] Exception occurred while sending video to Telegram: {e}")
        return False



def send_copy_paste_to_telegram(
    product_name: str,
    price: str,
    caption_text: str,
    hashtags: list,
    affiliate_url: str,
    storefront_url: str,
) -> bool:
    """
    Sends a second Telegram text message with everything formatted and ready to
    copy-paste directly into Instagram when scheduling the Reel.
    Includes: Reel title, full caption, hashtags, affiliate link, and storefront URL.
    """
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False

    hashtags_str = " ".join(hashtags) if hashtags else ""
    ig_title = f"{product_name} — Best Deal on Amazon India!"[:100]

    message = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "*COPY-PASTE FOR INSTAGRAM*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*REEL TITLE:*\n{ig_title}\n\n"
        f"*CAPTION:*\n{caption_text}\n\n"
        f"*HASHTAGS:*\n{hashtags_str}\n\n"
        f"*AFFILIATE LINK (put in bio):*\n{affiliate_url}\n\n"
        f"*STOREFRONT:*\n{storefront_url}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    if len(message) > 4096:
        message = message[:4090] + "..."

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if response.status_code == 200:
            logger.info("[telegram] Copy-paste caption message sent to Telegram.")
            return True
        else:
            logger.warning(f"[telegram] Caption message failed ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.warning(f"[telegram] Failed to send caption message: {e}")
        return False


def cleanup_generated_assets(video_id: str, output_dir: str, video_path: str) -> None:
    """
    Deletes ALL temporary files created during video generation for a given video_id.
    Called immediately after the video is successfully sent to Telegram.
    Keeps the laptop storage clean — nothing is kept once it's delivered.

    Deletes:
        - raw_script_{id}.txt     (LLM output)
        - cleaned_script_{id}.txt (cleaned phrases)
        - voiceover_{id}.wav      (synthesized audio)
        - captions_{id}.ass       (subtitle file)
        - visuals_{id}/           (B-roll clips + product images)
        - video_{id}.mp4          (final assembled video — already sent)
    Keeps:
        - queue.json, store.html, store_products.json, pipeline.log (persistent data)
    """
    temp_files = [
        os.path.join(output_dir, f"raw_script_{video_id}.txt"),
        os.path.join(output_dir, f"cleaned_script_{video_id}.txt"),
        os.path.join(output_dir, f"voiceover_{video_id}.wav"),
        os.path.join(output_dir, f"captions_{video_id}.ass"),
        video_path,
    ]

    deleted = []
    for tf in temp_files:
        if tf and os.path.exists(tf):
            try:
                os.remove(tf)
                deleted.append(os.path.basename(tf))
            except Exception as e:
                logger.warning(f"[telegram] Could not delete '{tf}': {e}")

    visuals_dir = os.path.join(output_dir, f"visuals_{video_id}")
    if os.path.exists(visuals_dir):
        try:
            shutil.rmtree(visuals_dir)
            deleted.append(f"visuals_{video_id}/")
        except Exception as e:
            logger.warning(f"[telegram] Could not delete visuals dir '{visuals_dir}': {e}")

    if deleted:
        logger.info(f"[telegram] Cleaned up {len(deleted)} temp asset(s) for '{video_id}': {', '.join(deleted)}")
    else:
        logger.info(f"[telegram] No temp assets to clean for '{video_id}'.")


def process_posted_callback(video_id: str) -> dict:
    """
    Updates review_status to 'posted' in queue.json and cleans up local video assets.
    NOTE: queue.json is written BEFORE any file deletion to prevent state corruption
    if the write fails after assets are already removed.
    """
    if not os.path.exists(QUEUE_FILE):
        return {"status": "error", "message": "queue.json not found"}

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        return {"status": "error", "message": f"Failed to read queue.json: {e}"}

    target_entry = None
    for entry in queue:
        if entry.get("video_id") == video_id:
            entry["review_status"] = "posted"
            target_entry = entry
            break

    if not target_entry:
        return {"status": "error", "message": f"video_id '{video_id}' not found in queue"}

    # Write queue.json FIRST — before touching the filesystem
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2)
        logger.info(f"[telegram] Updated review_status to 'posted' for video_id '{video_id}'")
    except Exception as e:
        logger.error(f"[telegram] Failed to write queue.json: {e}")
        # Do NOT proceed to delete files if we couldn't persist the status change
        return {"status": "error", "message": f"Could not write queue.json — files NOT deleted: {e}"}

    # Only delete local assets after the status is safely persisted
    video_path = target_entry.get("video_path", "")
    output_dir = os.path.dirname(video_path) if video_path else "output"

    deleted_items = []
    if video_path and os.path.exists(video_path):
        try:
            os.remove(video_path)
            deleted_items.append(video_path)
        except Exception as e:
            logger.warning(f"[telegram] Failed to delete video file '{video_path}': {e}")

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
                deleted_items.append(tf)
            except Exception as e:
                logger.warning(f"[telegram] Failed to delete temp file '{tf}': {e}")

    visuals_dir = os.path.join(output_dir, f"visuals_{video_id}")
    if os.path.exists(visuals_dir):
        try:
            shutil.rmtree(visuals_dir)
            deleted_items.append(visuals_dir)
        except Exception as e:
            logger.warning(f"[telegram] Failed to delete visuals dir '{visuals_dir}': {e}")

    logger.info(f"[telegram] Cleaned up {len(deleted_items)} asset(s) for video '{video_id}'")
    return {"status": "success", "video_id": video_id, "deleted_assets": len(deleted_items)}


@telegram_bp.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    """
    Flask route receiving updates from Telegram Bot API callback queries.
    """
    data = request.get_json(force=True, silent=True) or {}
    callback_query = data.get("callback_query")
    
    if not callback_query:
        return jsonify({"status": "ignored"}), 200

    cb_id = callback_query.get("id")
    cb_data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    msg_id = message.get("message_id")

    if cb_data.startswith("posted:"):
        video_id = cb_data.split("posted:")[1]
        logger.info(f"[telegram] Callback query received: 'posted' for video_id '{video_id}'")
        res = process_posted_callback(video_id)

        # Answer Telegram callback query notification banner
        token = config.TELEGRAM_BOT_TOKEN
        if token and cb_id:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                    json={"callback_query_id": cb_id, "text": "✅ Marked as Posted! Local files cleaned up."},
                    timeout=10
                )
            except Exception as e:
                logger.warning(f"[telegram] Failed to answer callback query: {e}")

            # Edit message text to confirm posted status
            if chat_id and msg_id:
                try:
                    orig_caption = message.get("caption", "")
                    new_caption = f"✅ *POSTED & ARCHIVED*\n\n{orig_caption}"
                    requests.post(
                        f"https://api.telegram.org/bot{token}/editMessageCaption",
                        json={
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "caption": new_caption,
                            "parse_mode": "Markdown",
                            "reply_markup": {"inline_keyboard": []}
                        },
                        timeout=10
                    )
                except Exception as e:
                    logger.warning(f"[telegram] Failed to edit Telegram message caption: {e}")

        return jsonify(res), 200

    return jsonify({"status": "ok"}), 200

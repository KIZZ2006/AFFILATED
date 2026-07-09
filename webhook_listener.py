"""
webhook_listener.py — Instagram Comment-to-DM Automation via Meta Private Replies.

This is a standalone Flask application meant to be deployed on an always-on host
(e.g., Render, Railway, Heroku). It continuously listens for Instagram comment 
webhooks and sends an automated Private Reply (DM) if the comment contains 
specific trigger keywords.

Features:
- Handles Meta Webhook Verification handshake.
- Parses Instagram comment webhook events.
- Checks for trigger keywords (case-insensitive substring match).
- Sends automated Private Replies with the storefront URL.
- Enforces Meta's rate limits (max 200 automated outbound messages per hour) using an in-memory sliding window.
- Fully decoupled from the GitHub Actions publishing queue.
"""

import os
import time
import logging
from flask import Flask, request, jsonify
import requests

import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("webhook_listener")

app = Flask(__name__)

# Register Telegram Review Loop Blueprint
try:
    from telegram_notifier import telegram_bp
    app.register_blueprint(telegram_bp)
    logger.info("[webhook_listener] Registered telegram_bp for Telegram callback review loop.")
except Exception as e:
    logger.warning(f"[webhook_listener] Could not register telegram_bp: {e}")

# Constants and Configuration
GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
TRIGGER_KEYWORDS = ["link", "price", "buy"]
MAX_REPLIES_PER_HOUR = 200

# In-memory store for rate limiting (timestamps of sent messages)
message_timestamps = []

def clean_old_timestamps():
    """Remove timestamps older than 1 hour (3600 seconds)."""
    global message_timestamps
    current_time = time.time()
    message_timestamps = [t for t in message_timestamps if current_time - t < 3600]

def can_send_message() -> bool:
    """Check if we are under the 200 messages/hour limit."""
    clean_old_timestamps()
    return len(message_timestamps) < MAX_REPLIES_PER_HOUR

def record_message_sent():
    """Record a timestamp for a sent message."""
    message_timestamps.append(time.time())

def get_storefront_url() -> str:
    """Constructs the storefront URL matching the pattern in storefront.py."""
    return f"{config.PUBLIC_HOST_ENDPOINT}/output/store.html"

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """
    Handle Meta's Webhook Verification handshake.
    Meta sends GET request with hub.mode, hub.challenge, and hub.verify_token.
    """
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    verify_token = config.WEBHOOK_VERIFY_TOKEN

    if mode and token:
        if mode == 'subscribe' and token == verify_token:
            logger.info("Webhook verification successful.")
            return challenge, 200
        else:
            logger.warning("Webhook verification failed: Invalid token.")
            return "Forbidden", 403
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    """
    Process incoming Instagram Webhook events.
    """
    data = request.json
    logger.info(f"Received webhook payload: {data}")

    if not data or data.get("object") != "instagram":
        return jsonify({"status": "ignored", "reason": "Not an instagram object"}), 200

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})
            
            # We are only interested in comments
            if field == "comments":
                comment_id = value.get("id")
                text = value.get("text", "").lower()
                from_user = value.get("from", {})
                
                # Ignore our own comments or empty text
                if not comment_id or not text or str(from_user.get("id")) == str(config.IG_USER_ID):
                    continue
                
                logger.info(f"Processing comment {comment_id}: '{text}'")

                # Check for trigger keywords
                if any(keyword in text for keyword in TRIGGER_KEYWORDS):
                    logger.info(f"Comment {comment_id} matched a trigger keyword.")
                    send_private_reply(comment_id)
                else:
                    logger.info(f"Comment {comment_id} did not match any trigger keywords.")

    return jsonify({"status": "success"}), 200

def send_private_reply(comment_id: str):
    """
    Sends a Private Reply to the comment via Instagram Graph API.
    """
    if not config.IG_ACCESS_TOKEN or not config.IG_USER_ID:
        logger.error("Missing IG_ACCESS_TOKEN or IG_USER_ID. Cannot send reply.")
        return

    if not can_send_message():
        logger.warning(f"Rate limit reached ({MAX_REPLIES_PER_HOUR}/hour). Skipping reply for comment {comment_id}.")
        return

    store_url = get_storefront_url()
    message_text = f"Hey! Thanks for your comment! 🎉 Here's the link you requested: {store_url}"

    url = f"{GRAPH_API_BASE}/{config.IG_USER_ID}/messages"
    payload = {
        "recipient": {
            "comment_id": comment_id
        },
        "message": {
            "text": message_text
        }
    }
    headers = {
        "Authorization": f"Bearer {config.IG_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.ok:
            logger.info(f"Successfully sent private reply for comment {comment_id}.")
            record_message_sent()
        else:
            logger.error(f"Failed to send private reply for comment {comment_id}. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        logger.error(f"Exception while sending private reply for comment {comment_id}: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Webhook Listener on port {port}...")
    app.run(host="0.0.0.0", port=port)

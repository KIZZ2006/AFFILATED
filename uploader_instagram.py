"""
uploader_instagram.py — Instagram Reels publisher via Meta Graph API (Resumable Upload).

Flow:
  1. Check token age guard (warns and aborts if token is >= 50 days old).
  2. Create resumable container: POST /{ig-user-id}/media with upload_type=resumable & media_type=REELS.
  3. Upload video bytes directly to rupload.facebook.com via binary POST stream.
  4. Poll /{container_id}?fields=status_code until FINISHED.
  5. Publish container: POST /{ig-user-id}/media_publish.
  6. Fetch and return the permalink.
"""

import logging
import os
import time
from datetime import date, datetime
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_API_VERSION = "v19.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

POLL_INTERVAL = 8
POLL_TIMEOUT = 600
TOKEN_WARN_DAYS = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_token_age() -> None:
    """
    Compare today's date against IG_TOKEN_ISSUED_DATE.
    Log a warning and raise RuntimeError if token is >= 50 days old.
    """
    issued_str = config.IG_TOKEN_ISSUED_DATE.strip()
    if not issued_str:
        logger.debug("[instagram] IG_TOKEN_ISSUED_DATE not set — skipping age check.")
        return

    try:
        issued_date = datetime.strptime(issued_str, "%Y-%m-%d").date()
    except ValueError:
        logger.warning(
            f"[instagram] IG_TOKEN_ISSUED_DATE '{issued_str}' is not in YYYY-MM-DD format. "
            "Skipping age check."
        )
        return

    age_days = (date.today() - issued_date).days
    logger.info(f"[instagram] Token age: {age_days} day(s) (issued {issued_str}).")

    if age_days >= TOKEN_WARN_DAYS:
        raise RuntimeError(
            f"[instagram] CRITICAL — Instagram access token is {age_days} days old "
            f"(threshold: {TOKEN_WARN_DAYS} days). Long-lived tokens expire at 60 days. "
            "Renew the token manually in Meta Business Suite before running the pipeline again."
        )


def _check_ig_graph_response(response: requests.Response, context: str) -> dict:
    """Parse Graph API response and raise RuntimeError on errors."""
    try:
        body = response.json()
    except Exception:
        raise RuntimeError(
            f"[instagram] {context}: non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:500]}"
        )

    if response.status_code == 429:
        raise RuntimeError(
            f"[instagram] {context}: Rate-limited by Graph API (HTTP 429)."
        )

    if "error" in body:
        err = body["error"]
        code = err.get("code", "?")
        subcode = err.get("error_subcode", "?")
        msg = err.get("message", "unknown error")
        fbtrace = err.get("fbtrace_id", "?")
        raise RuntimeError(
            f"[instagram] {context}: Graph API error {code}/{subcode} — {msg} (fbtrace_id={fbtrace})"
        )

    if not response.ok:
        raise RuntimeError(
            f"[instagram] {context}: HTTP {response.status_code} — {response.text[:500]}"
        )

    return body


def _create_resumable_container(caption: str) -> tuple[str, str]:
    """
    Step 1: POST /{ig-user-id}/media with upload_type=resumable and media_type=REELS.
    Returns (container_id, upload_uri).
    """
    url = f"{GRAPH_API_BASE}/{config.IG_USER_ID}/media"
    payload = {
        "upload_type": "resumable",
        "media_type": "REELS",
        "caption": caption,
        "share_to_feed": True,
        "access_token": config.IG_ACCESS_TOKEN,
    }

    logger.info("[instagram] Step 1: Initializing resumable Reels media container...")
    response = requests.post(url, data=payload, timeout=60)
    body = _check_ig_graph_response(response, context="create-resumable-container")

    container_id = body.get("id")
    upload_uri = body.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{GRAPH_API_VERSION}/{container_id}"

    if not container_id:
        raise RuntimeError(f"[instagram] Resumable container creation failed. Body: {body}")

    logger.info(f"[instagram] Container initialized. ID={container_id}, URI={upload_uri}")
    return container_id, upload_uri


def _upload_bytes_resumable(container_id: str, upload_uri: str, video_path: str) -> None:
    """
    Step 2: Stream local video file bytes directly to rupload.facebook.com endpoint.
    """
    file_size = os.path.getsize(video_path)
    file_size_mb = file_size / (1024 * 1024)
    logger.info(f"[instagram] Step 2: Uploading {file_size_mb:.2f} MB binary payload to rupload endpoint...")

    headers = {
        "Authorization": f"OAuth {config.IG_ACCESS_TOKEN}",
        "file_size": str(file_size),
        "offset": "0",
        "Content-Type": "application/octet-stream",
    }

    with open(video_path, "rb") as fh:
        response = requests.post(upload_uri, headers=headers, data=fh, timeout=300)

    if not response.ok:
        raise RuntimeError(
            f"[instagram] Resumable binary upload failed (HTTP {response.status_code}): {response.text[:500]}"
        )

    logger.info("[instagram] Binary video payload successfully uploaded to rupload server.")


def _poll_container_status(container_id: str) -> None:
    """
    Step 3: Poll /{container_id}?fields=status_code until status is FINISHED.
    """
    url = f"{GRAPH_API_BASE}/{container_id}"
    params = {
        "fields": "status_code,status",
        "access_token": config.IG_ACCESS_TOKEN,
    }

    deadline = time.monotonic() + POLL_TIMEOUT
    attempt = 0

    logger.info(f"[instagram] Step 3: Polling container {container_id} status...")

    while time.monotonic() < deadline:
        attempt += 1
        response = requests.get(url, params=params, timeout=30)
        body = _check_ig_graph_response(response, context=f"poll-status (attempt {attempt})")

        status_code = body.get("status_code", "UNKNOWN")
        status_detail = body.get("status", "")
        logger.info(f"[instagram] Poll attempt {attempt}: status_code={status_code} ({status_detail})")

        if status_code == "FINISHED":
            logger.info("[instagram] Container processing FINISHED. Ready to publish.")
            return

        if status_code in ("EXPIRED", "ERROR"):
            raise RuntimeError(f"[instagram] Container {container_id} failed with status '{status_code}': {status_detail}")

        time.sleep(POLL_INTERVAL)

    raise RuntimeError(f"[instagram] Container {container_id} timed out after {POLL_TIMEOUT}s.")


def _publish_container(container_id: str) -> str:
    """
    Step 4: POST /{ig-user-id}/media_publish.
    Returns published media_id.
    """
    url = f"{GRAPH_API_BASE}/{config.IG_USER_ID}/media_publish"
    payload = {
        "creation_id": container_id,
        "access_token": config.IG_ACCESS_TOKEN,
    }

    logger.info(f"[instagram] Step 4: Publishing container {container_id}...")
    response = requests.post(url, data=payload, timeout=60)
    body = _check_ig_graph_response(response, context="media_publish")

    media_id = body.get("id")
    if not media_id:
        raise RuntimeError(f"[instagram] media_publish returned no 'id'. Body: {body}")

    logger.info(f"[instagram] Reel published! media_id={media_id}")
    return media_id


def _fetch_permalink(media_id: str) -> str:
    """Fetch published Reel permalink."""
    url = f"{GRAPH_API_BASE}/{media_id}"
    params = {
        "fields": "permalink,shortcode",
        "access_token": config.IG_ACCESS_TOKEN,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        body = _check_ig_graph_response(response, context="fetch-permalink")
        if body.get("permalink"):
            return body["permalink"]
        if body.get("shortcode"):
            return f"https://www.instagram.com/reel/{body['shortcode']}/"
    except Exception as e:
        logger.warning(f"[instagram] Could not fetch permalink for media_id={media_id}: {e}")

    return f"https://www.instagram.com/p/{media_id}/"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_to_instagram(video_path: str, caption: str) -> str:
    """
    Publish a local MP4 video to Instagram Reels using Meta's resumable upload flow.
    """
    if not config.IG_ACCESS_TOKEN:
        raise ValueError("[instagram] IG_ACCESS_TOKEN is not set in environment or config.")
    if not config.IG_USER_ID:
        raise ValueError("[instagram] IG_USER_ID is not set in environment or config.")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"[instagram] Video file not found: {video_path}")

    # 1. Token age guard
    _check_token_age()

    # 2. Resumable container initialization
    container_id, upload_uri = _create_resumable_container(caption)

    # 3. Stream binary video bytes directly to Meta
    _upload_bytes_resumable(container_id, upload_uri, video_path)

    # 4. Poll processing status
    _poll_container_status(container_id)

    # 5. Publish Reel
    media_id = _publish_container(container_id)

    # 6. Fetch permalink
    permalink = _fetch_permalink(media_id)
    logger.info(f"[instagram] Instagram Reel successfully published: {permalink}")
    return permalink

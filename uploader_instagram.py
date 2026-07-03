"""
uploader_instagram.py — Instagram Reels publisher via Meta Graph API.

Two-step flow:
  1. Upload the local MP4 to a public HTTP host (configured via PUBLIC_HOST_ENDPOINT).
  2. POST to /{ig-user-id}/media  →  get creation_id.
  3. Poll /{creation_id}?fields=status_code until FINISHED.
  4. POST to /{ig-user-id}/media_publish  →  get media_id.
  5. Fetch the permalink and return it.

Token age guard:
  Long-lived Instagram tokens expire after 60 days.  If IG_TOKEN_ISSUED_DATE
  is set and the token is ≥ 50 days old, a prominent warning is logged and
  the upload is aborted early — automated refresh is not attempted because
  the refresh itself requires a valid (non-expired) token and a browser-based
  re-auth for the initial grant.

Public video hosting:
  The Graph API pulls the video from a URL; it cannot accept a local file.
  This module uploads the MP4 to the endpoint at PUBLIC_HOST_ENDPOINT using
  a multipart POST.  The server is expected to return the public URL of the
  hosted file in a JSON body: {"url": "https://..."}  OR the file is served
  at PUBLIC_HOST_ENDPOINT/<filename> after a successful PUT.  Both strategies
  are tried in order.
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

# How long to wait between status polls (seconds)
POLL_INTERVAL = 8
# Maximum total time to wait for Instagram to finish processing (seconds)
POLL_TIMEOUT = 600
# Token age threshold at which we warn the user (days before the 60-day expiry)
TOKEN_WARN_DAYS = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_token_age() -> None:
    """
    Compare today's date against IG_TOKEN_ISSUED_DATE.
    Log a prominent warning (and raise RuntimeError) if the token is >= 50 days old.
    Long-lived IG tokens expire at exactly 60 days; 50 days gives a 10-day buffer.
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
            "Renew the token manually in Meta Business Suite or via the Graph API refresh "
            "endpoint BEFORE running the pipeline again. Upload aborted to avoid an "
            "expired-token API error mid-publish."
        )


def _check_ig_graph_response(response: requests.Response, context: str) -> dict:
    """
    Parse a Graph API response.  Raise a descriptive RuntimeError for any
    error condition (HTTP non-200, Graph error object, rate limits).

    Returns the parsed JSON dict on success.
    """
    try:
        body = response.json()
    except Exception:
        raise RuntimeError(
            f"[instagram] {context}: non-JSON response (HTTP {response.status_code}): "
            f"{response.text[:500]}"
        )

    if response.status_code == 429:
        raise RuntimeError(
            f"[instagram] {context}: Rate-limited by Graph API (HTTP 429). "
            "Wait at least 1 hour before retrying."
        )

    if "error" in body:
        err = body["error"]
        code = err.get("code", "?")
        subcode = err.get("error_subcode", "?")
        msg = err.get("message", "unknown error")
        fbtrace = err.get("fbtrace_id", "?")
        # Specific guidance for common error codes
        if code == 190:
            hint = "Access token is invalid or expired. Renew it in Meta Business Suite."
        elif code == 10:
            hint = "Missing permission scope. Ensure instagram_content_publish is granted."
        elif code == 32 or code == 4:
            hint = "API rate limit reached. Reduce call frequency."
        else:
            hint = "Check Meta Graph API docs for this error code."
        raise RuntimeError(
            f"[instagram] {context}: Graph API error {code}/{subcode} — {msg}. "
            f"Hint: {hint} (fbtrace_id={fbtrace})"
        )

    if not response.ok:
        raise RuntimeError(
            f"[instagram] {context}: HTTP {response.status_code} — {response.text[:500]}"
        )

    return body


def _upload_video_to_public_host(video_path: str) -> str:
    """
    Upload the local MP4 to the configured PUBLIC_HOST_ENDPOINT.

    Strategy A — multipart POST:
      POST PUBLIC_HOST_ENDPOINT  with file= field.
      Expects JSON response: {"url": "https://..."}.

    Strategy B — PUT:
      PUT PUBLIC_HOST_ENDPOINT/<filename>.
      Assumes the server serves the file back at that same URL.

    Returns the public HTTPS URL of the hosted file.
    """
    if not config.PUBLIC_HOST_ENDPOINT:
        raise ValueError(
            "[instagram] PUBLIC_HOST_ENDPOINT is not set in config/.env. "
            "Instagram Graph API requires a public URL for the video. "
            "Set this to an ngrok tunnel URL or your own public server."
        )

    endpoint = config.PUBLIC_HOST_ENDPOINT.rstrip("/")
    filename = Path(video_path).name
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    logger.info(
        f"[instagram] Uploading {filename} ({file_size_mb:.1f} MB) to public host: {endpoint}"
    )

    # If using ngrok, the file is already being served locally, construct path relative to server root
    if "ngrok" in endpoint:
        rel_path = os.path.relpath(video_path).replace("\\", "/")
        put_url = f"{endpoint}/{rel_path}"
        logger.info(f"[instagram] Ngrok detected. Public URL: {put_url}")
        return put_url

    # Strategy A: multipart POST
    try:
        with open(video_path, "rb") as fh:
            response = requests.post(
                endpoint,
                files={"file": (filename, fh, "video/mp4")},
                timeout=300,
            )
        if response.ok:
            try:
                body = response.json()
                public_url = body.get("url") or body.get("public_url") or body.get("link")
                if public_url:
                    logger.info(f"[instagram] Strategy A (POST) succeeded. Public URL: {public_url}")
                    return public_url
            except Exception:
                pass
            # If JSON parsing failed but status was ok, assume Strategy B URL shape
            logger.debug("[instagram] Strategy A responded OK but returned no URL field. Trying URL inference.")
    except Exception as e:
        logger.debug(f"[instagram] Strategy A (multipart POST) raised: {e}. Trying Strategy B.")

    # Strategy B: PUT and infer the URL
    put_url = f"{endpoint}/{filename}"
    try:
        with open(video_path, "rb") as fh:
            response = requests.put(
                put_url,
                data=fh,
                headers={"Content-Type": "video/mp4"},
                timeout=300,
            )
        if response.ok:
            logger.info(f"[instagram] Strategy B (PUT) succeeded. Public URL: {put_url}")
            return put_url
    except Exception as e:
        raise RuntimeError(
            f"[instagram] Both upload strategies failed. "
            f"Ensure PUBLIC_HOST_ENDPOINT ({endpoint}) is reachable and accepts file uploads. "
            f"Last error: {e}"
        )

    raise RuntimeError(
        f"[instagram] Both upload strategies returned a non-OK HTTP status. "
        f"Check PUBLIC_HOST_ENDPOINT ({endpoint}) server configuration."
    )


def _create_media_container(public_video_url: str, caption: str) -> str:
    """
    Step 1 of the Graph API Reels publish flow.
    POST /{ig-user-id}/media with media_type=REELS.
    Returns the creation_id string.
    """
    url = f"{GRAPH_API_BASE}/{config.IG_USER_ID}/media"
    payload = {
        "media_type": "REELS",
        "video_url": public_video_url,
        "caption": caption,
        "share_to_feed": True,
        "access_token": config.IG_ACCESS_TOKEN,
    }

    logger.info("[instagram] Step 1: Creating Reels media container...")
    response = requests.post(url, data=payload, timeout=60)
    body = _check_ig_graph_response(response, context="create-media-container")

    creation_id = body.get("id")
    if not creation_id:
        raise RuntimeError(
            f"[instagram] create-media-container succeeded but returned no 'id'. Body: {body}"
        )

    logger.info(f"[instagram] Container created. creation_id={creation_id}")
    return creation_id


def _poll_container_status(creation_id: str) -> None:
    """
    Step 2: Poll /{creation_id}?fields=status_code until status is FINISHED.
    Raises RuntimeError on EXPIRED or if polling times out.
    """
    url = f"{GRAPH_API_BASE}/{creation_id}"
    params = {
        "fields": "status_code,status",
        "access_token": config.IG_ACCESS_TOKEN,
    }

    deadline = time.monotonic() + POLL_TIMEOUT
    attempt = 0

    logger.info(
        f"[instagram] Step 2: Polling container {creation_id} status "
        f"(timeout={POLL_TIMEOUT}s, interval={POLL_INTERVAL}s)..."
    )

    while time.monotonic() < deadline:
        attempt += 1
        try:
            response = requests.get(url, params=params, timeout=30)
            body = _check_ig_graph_response(response, context=f"poll-status (attempt {attempt})")
        except RuntimeError as e:
            # Re-raise immediately — Graph API errors during polling are fatal
            raise e

        status_code = body.get("status_code", "UNKNOWN")
        status_detail = body.get("status", "")
        logger.info(
            f"[instagram] Poll attempt {attempt}: status_code={status_code} ({status_detail})"
        )

        if status_code == "FINISHED":
            logger.info("[instagram] Container processing FINISHED. Ready to publish.")
            return

        if status_code == "EXPIRED":
            raise RuntimeError(
                f"[instagram] Media container {creation_id} EXPIRED before processing completed. "
                "This usually means the video URL became inaccessible or the video format is "
                "unsupported. Ensure the public URL is reachable and the file is a valid MP4."
            )

        if status_code == "ERROR":
            raise RuntimeError(
                f"[instagram] Media container {creation_id} entered ERROR state: {status_detail}. "
                "Check that the video meets Instagram's Reels requirements: H.264, AAC audio, "
                "9:16 aspect ratio, under 15 minutes, minimum 720p."
            )

        # IN_PROGRESS or any other transient state — wait and retry
        time.sleep(POLL_INTERVAL)

    raise RuntimeError(
        f"[instagram] Container {creation_id} did not reach FINISHED state within "
        f"{POLL_TIMEOUT} seconds after {attempt} poll attempts. "
        "Instagram may be experiencing delays. Try publishing manually from the Meta dashboard."
    )


def _publish_container(creation_id: str) -> str:
    """
    Step 3: POST /{ig-user-id}/media_publish.
    Returns the published media_id.
    """
    url = f"{GRAPH_API_BASE}/{config.IG_USER_ID}/media_publish"
    payload = {
        "creation_id": creation_id,
        "access_token": config.IG_ACCESS_TOKEN,
    }

    logger.info(f"[instagram] Step 3: Publishing container {creation_id}...")
    response = requests.post(url, data=payload, timeout=60)
    body = _check_ig_graph_response(response, context="media_publish")

    media_id = body.get("id")
    if not media_id:
        raise RuntimeError(
            f"[instagram] media_publish succeeded but returned no 'id'. Body: {body}"
        )

    logger.info(f"[instagram] Reel published. media_id={media_id}")
    return media_id


def _fetch_permalink(media_id: str) -> str:
    """
    Fetch the permanent permalink for the published Reel.
    Falls back to a constructed URL if the field is unavailable.
    """
    url = f"{GRAPH_API_BASE}/{media_id}"
    params = {
        "fields": "permalink,shortcode",
        "access_token": config.IG_ACCESS_TOKEN,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        body = _check_ig_graph_response(response, context="fetch-permalink")
        permalink = body.get("permalink")
        if permalink:
            return permalink
        shortcode = body.get("shortcode")
        if shortcode:
            return f"https://www.instagram.com/reel/{shortcode}/"
    except Exception as e:
        logger.warning(f"[instagram] Could not fetch permalink for media_id={media_id}: {e}")

    # Graceful fallback — the media ID is still useful for audit logs
    return f"https://www.instagram.com/p/{media_id}/"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_to_instagram(video_path: str, caption: str) -> str:
    """
    Publish a local MP4 video to Instagram Reels via the Meta Graph API.

    Steps:
      0. Validate prerequisites (token, credentials, file existence).
      1. Check token age — abort if ≥ 50 days old with a clear message.
      2. Upload the local MP4 to PUBLIC_HOST_ENDPOINT to get a public URL.
      3. Create a Reels media container (POST to /{ig-user-id}/media).
      4. Poll the container status until FINISHED (or timeout/error).
      5. Publish the container (POST to /{ig-user-id}/media_publish).
      6. Fetch and return the published Reel's permalink.

    Args:
        video_path: Absolute or relative path to the finished MP4.
        caption:    Caption text (may include hashtags).

    Returns:
        Permanent permalink URL of the published Reel (e.g. https://www.instagram.com/reel/...).

    Raises:
        ValueError:        If required config keys are missing.
        FileNotFoundError: If video_path does not exist.
        RuntimeError:      On token age violation, API errors, container failures, or timeout.
    """
    # ------------------------------------------------------------------
    # 0. Prerequisite checks
    # ------------------------------------------------------------------
    errors = []
    if not config.IG_ACCESS_TOKEN:
        errors.append("IG_ACCESS_TOKEN is not set in config/.env")
    if not config.IG_USER_ID:
        errors.append("IG_USER_ID is not set in config/.env")
    if not config.PUBLIC_HOST_ENDPOINT:
        errors.append(
            "PUBLIC_HOST_ENDPOINT is not set in config/.env "
            "(required so the Graph API can pull the video from a public URL)"
        )
    if errors:
        raise ValueError(
            "[instagram] Missing required configuration:\n  " + "\n  ".join(errors)
        )

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"[instagram] Video file not found: {video_path}")

    # ------------------------------------------------------------------
    # 1. Token age guard
    # ------------------------------------------------------------------
    _check_token_age()

    # ------------------------------------------------------------------
    # 2. Upload video to public host
    # ------------------------------------------------------------------
    public_url = _upload_video_to_public_host(video_path)

    # ------------------------------------------------------------------
    # 3. Create media container
    # ------------------------------------------------------------------
    creation_id = _create_media_container(public_url, caption)

    # ------------------------------------------------------------------
    # 4. Poll until FINISHED
    # ------------------------------------------------------------------
    _poll_container_status(creation_id)

    # ------------------------------------------------------------------
    # 5. Publish container
    # ------------------------------------------------------------------
    media_id = _publish_container(creation_id)

    # ------------------------------------------------------------------
    # 6. Fetch permalink
    # ------------------------------------------------------------------
    permalink = _fetch_permalink(media_id)
    logger.info(f"[instagram] Instagram Reel live at: {permalink}")
    return permalink

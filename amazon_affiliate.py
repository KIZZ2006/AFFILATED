"""
amazon_affiliate.py — Automated Amazon Affiliate URL generator & ASIN link builder.

Uses AMAZON_ASSOCIATE_ID from config.py (sourced from .env) to dynamically convert
any product name, ASIN, or raw Amazon URL into a tracked affiliate purchase link.

Single source of truth: all URL-building goes through generate_affiliate_link().
No other file should construct a raw amazon.com URL independently.
"""

import urllib.parse
import logging
import config

logger = logging.getLogger(__name__)


def _get_tag() -> str:
    """
    Returns the Amazon Associate tag from centralized config.
    Raises ValueError if the tag is missing or empty so that untagged links
    are never silently produced.
    """
    tag = config.AMAZON_ASSOCIATE_ID.strip()
    if not tag:
        logger.error(
            "[affiliate] AMAZON_ASSOCIATE_ID is missing or empty in config/.env. "
            "Links will NOT be tagged — affiliate revenue tracking is broken. "
            "Set AMAZON_ASSOCIATE_ID in your .env file immediately."
        )
        raise ValueError(
            "AMAZON_ASSOCIATE_ID is not configured. "
            "Cannot generate a tagged affiliate link. "
            "Add it to your .env file: AMAZON_ASSOCIATE_ID=yourtag-20"
        )
    return tag


def generate_affiliate_link(product_name: str, raw_url_or_asin: str | None = None) -> str:
    """
    Generates a tracked Amazon Affiliate link for a given product name or ASIN/URL.

    Args:
        product_name: Name of the product (e.g. "Electric Coffee Grinder").
        raw_url_or_asin: Optional raw Amazon URL or 10-character ASIN.

    Returns:
        str: Fully formatted, tracked Amazon Associate URL.

    Raises:
        ValueError: If AMAZON_ASSOCIATE_ID is not configured.
    """
    tag = _get_tag()

    if raw_url_or_asin:
        val = raw_url_or_asin.strip()
        # If it's a 10-char ASIN (e.g. B08N5WRWNW)
        if len(val) == 10 and val.isalnum():
            return f"https://www.amazon.in/dp/{val}/?tag={tag}"

        # If it's a full Amazon URL (any region) — inject/overwrite tag param
        if "amazon." in val:
            parsed = urllib.parse.urlparse(val)
            query = urllib.parse.parse_qs(parsed.query)
            query["tag"] = [tag]
            new_query = urllib.parse.urlencode(query, doseq=True)
            return urllib.parse.urlunparse(parsed._replace(query=new_query))

    # Fallback / Default: Search Query Affiliate Link (Defaulting to Amazon India)
    encoded_query = urllib.parse.quote_plus(product_name.strip())
    return f"https://www.amazon.in/s?k={encoded_query}&tag={tag}"


def verify_affiliate_link(url: str) -> bool:
    """
    Manual-check helper: verifies that a generated URL contains the correct
    affiliate tag parameter.

    Usage:
        from amazon_affiliate import generate_affiliate_link, verify_affiliate_link
        link = generate_affiliate_link("Smart Blender")
        assert verify_affiliate_link(link), "Tag missing from affiliate link!"

    Returns:
        True if the tag param is present and matches config.AMAZON_ASSOCIATE_ID.
        False otherwise (also logs a warning).
    """
    tag = config.AMAZON_ASSOCIATE_ID.strip()
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    tag_values = params.get("tag", [])

    if not tag_values:
        logger.warning(f"[affiliate] verify_affiliate_link: No 'tag' param found in URL: {url}")
        return False

    if tag_values[0] != tag:
        logger.warning(
            f"[affiliate] verify_affiliate_link: Tag mismatch. "
            f"Expected '{tag}', got '{tag_values[0]}'. URL: {url}"
        )
        return False

    logger.info(f"[affiliate] verify_affiliate_link: OK — tag='{tag}' confirmed in URL.")
    return True


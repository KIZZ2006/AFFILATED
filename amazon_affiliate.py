"""
amazon_affiliate.py — Automated Amazon Affiliate URL generator & ASIN link builder.

Uses AMAZON_ASSOCIATE_ID from .env to dynamically convert any product name,
ASIN, or raw Amazon URL into a tracked affiliate purchase link.
"""

import os
import urllib.parse
import config

ASSOCIATE_ID = os.getenv("AMAZON_ASSOCIATE_ID", "giftgaller036-21")


def generate_affiliate_link(product_name: str, raw_url_or_asin: str | None = None) -> str:
    """
    Generates a tracked Amazon Affiliate link for a given product name or ASIN/URL.

    Args:
        product_name: Name of the product (e.g. "Electric Coffee Grinder").
        raw_url_or_asin: Optional raw Amazon URL or 10-character ASIN.

    Returns:
        str: Fully formatted, tracked Amazon Associate URL.
    """
    tag = ASSOCIATE_ID.strip()

    if raw_url_or_asin:
        val = raw_url_or_asin.strip()
        # If it's a 10-char ASIN (e.g. B08N5WRWNW)
        if len(val) == 10 and val.isalnum():
            return f"https://www.amazon.com/dp/{val}/?tag={tag}"

        # If it's a full Amazon URL
        if "amazon.com" in val:
            parsed = urllib.parse.urlparse(val)
            query = urllib.parse.parse_qs(parsed.query)
            query["tag"] = [tag]
            new_query = urllib.parse.urlencode(query, doseq=True)
            return urllib.parse.urlunparse(parsed._replace(query=new_query))

    # Fallback / Default: Search Query Affiliate Link
    encoded_query = urllib.parse.quote_plus(product_name.strip())
    return f"https://www.amazon.com/s?k={encoded_query}&tag={tag}"

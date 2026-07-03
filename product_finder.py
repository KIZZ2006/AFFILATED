import json
import os
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)

CATALOG_PATH = "product_catalog.json"

CATEGORIES = [
    "kitchen gadgets",
    "home office accessories",
    "tech and desk gadgets",
    "fitness tech",
    "car accessories",
    "home decor and ambient lighting",
    "beauty and personal care gadgets"
]


def get_next_product() -> dict:
    """
    Returns the next product to be featured in the daily video campaign.
    
    1. Looks for the first unpublished product in product_catalog.json.
    2. If all products are published (or file is empty), uses AI to discover a new product.
    3. Marks the product as published with execution timestamp.
    
    Returns:
        dict: Product dict with keys ('product', 'price', 'features', 'niche')
    """
    catalog = []
    if os.path.exists(CATALOG_PATH):
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read product catalog: {e}")

    # Search for first unpublished product
    for item in catalog:
        if not item.get("published", False):
            item["published"] = True
            item["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_catalog(catalog)
            logger.info(f"[product_finder] Selected product from queue: '{item['product']}'")
            return item

    # If no unpublished product left in catalog, generate new viral product via AI
    logger.info("[product_finder] Catalog queue empty or fully published. Discovering new product via AI...")
    new_item = _discover_ai_product(catalog)
    catalog.append(new_item)
    _save_catalog(catalog)
    return new_item


def _discover_ai_product(existing_catalog: list[dict]) -> dict:
    """Uses LLM to discover a trending, real high-converting Amazon product."""
    import random
    from script_generator import _try_groq, _try_nim, _try_gemini

    niche = random.choice(CATEGORIES)
    existing_names = [p.get("product", "") for p in existing_catalog]

    prompt = f"""Discover ONE real, trending, high-converting viral product on Amazon in the '{niche}' category.
Avoid these previously used products: {', '.join(existing_names[-15:])}

Output ONLY valid JSON in exactly this format:
{{
  "product": "Product Name",
  "price": "$XX.YY",
  "features": "feature 1, feature 2, feature 3, feature 4",
  "niche": "{niche}"
}}"""

    for provider_fn in (_try_groq, _try_nim, _try_gemini):
        try:
            raw_resp = provider_fn(prompt)
            start_idx = raw_resp.find("{")
            end_idx = raw_resp.rfind("}") + 1
            if start_idx != -1 and end_idx != -1:
                data = json.loads(raw_resp[start_idx:end_idx])
                data["published"] = True
                data["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"[product_finder] AI discovered new product: '{data.get('product')}' ({data.get('price')})")
                return data
        except Exception as e:
            logger.warning(f"[product_finder] Provider attempt failed: {e}")

    # Emergency Fallback
    return {
        "product": "Smart Motion Sensor Night Light",
        "price": "$16.99",
        "features": "magnetic mount, rechargeable battery, motion detection, warm glow",
        "niche": "home decor",
        "published": True,
        "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def _save_catalog(catalog: list[dict]) -> None:
    """Saves updated catalog list to product_catalog.json."""
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

import json
import os
import logging
import re
import urllib.parse
from datetime import datetime
import difflib
import config

logger = logging.getLogger(__name__)

CATALOG_PATH = "product_catalog.json"

CATEGORIES = [
    "kitchen appliances and gadgets",          # 9% commission on Amazon.in
    "home decor and furniture",                # 9% commission
    "toys and baby products",                  # 9% commission
    "apparel and fashion accessories",         # 9% commission
    "automotive and car accessories",          # 9% commission
    "sports and outdoors",                     # 9% commission
    "lawn and garden"                          # 9% commission
]

def _normalize_catalog(catalog: list[dict]) -> list[dict]:
    for item in catalog:
        if "status" not in item:
            item["status"] = "used" if item.get("published", False) else "pending"
    return catalog


def resolve_amazon_in_asin(product_name: str) -> str | None:
    """
    Searches Amazon.in for a product name and returns the top result's ASIN.
    Uses Playwright to bypass bot detection.
    Returns None if no ASIN is found or the request fails.
    """
    from playwright.sync_api import sync_playwright
    from playwright_stealth.stealth import Stealth
    try:
        query = urllib.parse.quote_plus(product_name.strip())
        url = f"https://www.amazon.in/s?k={query}"
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            
            try:
                stealth = Stealth()
                stealth.apply_stealth_sync(page)
            except Exception as e:
                logger.warning(f"[product_finder] Could not apply stealth to Playwright: {e}")
                
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            # Brief wait for elements
            try:
                page.wait_for_selector('[data-asin]', timeout=3000)
            except:
                pass
                
            content = page.content()
            browser.close()
            
            # Find data-asins
            matches = re.findall(r'data-asin="([A-Z0-9]{10})"', content)
            valid_asins = [m for m in matches if m.strip()]
            if valid_asins:
                asin = valid_asins[0]
                logger.info(f"[product_finder] Resolved ASIN {asin} for '{product_name}'")
                return asin
                
        logger.warning(f"[product_finder] Could not resolve ASIN for '{product_name}'")
    except Exception as e:
        logger.warning(f"[product_finder] ASIN resolution failed for '{product_name}': {e}")
    return None


def get_next_product() -> dict:
    """
    Returns the next pending product. Auto-replenishes if pending count is low.
    """
    catalog = []
    if os.path.exists(CATALOG_PATH):
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read product catalog: {e}")

    catalog = _normalize_catalog(catalog)
    pending_items = [p for p in catalog if p.get("status") == "pending"]

    if len(pending_items) < 20:
        logger.info(f"[product_finder] Pending products below threshold ({len(pending_items)} < 20). Triggering auto-replenishment...")
        catalog = _replenish_catalog(catalog)
        pending_items = [p for p in catalog if p.get("status") == "pending"]

    if pending_items:
        selected = pending_items[0]
        logger.info(f"[product_finder] Selected product from queue: '{selected['product']}'")
        return selected

    # Emergency Fallback if AI fails completely
    emergency = {
        "product": "Smart Motion Sensor Night Light",
        "price": "$16.99",
        "features": "magnetic mount, rechargeable battery, motion detection, warm glow",
        "niche": "home decor",
        "status": "pending"
    }
    catalog.append(emergency)
    _save_catalog(catalog)
    return emergency

def _replenish_catalog(existing_catalog: list[dict]) -> list[dict]:
    """Generates a batch of new products across niches to fill the catalog to ~100-200 items."""
    import random

    existing_names = [p.get("product", "").lower() for p in existing_catalog]
    new_products_added = 0

    # We will run 3 batches of 40 products to aim for ~120 total products.
    for batch_num in range(3):
        niches_str = ", ".join(random.sample(CATEGORIES, 3))
        prompt = f"""Generate a JSON array of 40 real, trending, high-converting viral products \
popular on Amazon India (amazon.in) across these niches: {niches_str}.

RULES:
- All prices MUST be in Indian Rupees using the ₹ symbol (e.g. ₹299, ₹1499, ₹4,999).
- Products must be available and relevant to Indian buyers.
- Do NOT wrap output in markdown code blocks (no ```json). Output ONLY the raw JSON array.

Format MUST be exactly like this:
[
  {{
    "product": "Product Name",
    "price": "₹XXXX",
    "features": "feature 1, feature 2, feature 3, feature 4",
    "niche": "category name"
  }}
]"""

        batch_results = []
        try:
            from groq import Groq
            if config.GROQ_API_KEY:
                client = Groq(api_key=config.GROQ_API_KEY)
                resp = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.8,
                    max_tokens=2500,
                )
                raw_resp = resp.choices[0].message.content
                start_idx = raw_resp.find("[")
                end_idx = raw_resp.rfind("]") + 1
                if start_idx != -1 and end_idx != -1:
                    batch_results = json.loads(raw_resp[start_idx:end_idx])
            else:
                logger.warning("[product_finder] GROQ_API_KEY missing, skipping AI replenishment.")
                break
        except Exception as e:
            logger.warning(f"[product_finder] AI batch {batch_num+1} failed: {e}")
                
        if not batch_results:
            continue
            
        for item in batch_results:
            prod_name = item.get("product", "")
            if not prod_name: continue
            
            # Fuzzy deduplication
            is_dup = False
            for exist_name in existing_names:
                if difflib.SequenceMatcher(None, prod_name.lower(), exist_name).ratio() > 0.75:
                    is_dup = True
                    break
            
            if not is_dup:
                item["status"] = "pending"
                # Auto-resolve ASIN for direct Amazon India product page links
                if not item.get("asin"):
                    resolved = resolve_amazon_in_asin(item.get("product", ""))
                    if resolved:
                        item["asin"] = resolved
                existing_catalog.append(item)
                existing_names.append(prod_name.lower())
                new_products_added += 1

    logger.info(f"[product_finder] Replenishment complete. Added {new_products_added} new unique products.")
    _save_catalog(existing_catalog)
    return existing_catalog

def mark_product_used(product_name: str) -> None:
    """Marks a product as used after a successful pipeline run."""
    catalog = []
    if os.path.exists(CATALOG_PATH):
        try:
            with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except Exception:
            return
            
    catalog = _normalize_catalog(catalog)
    
    for item in catalog:
        if item.get("product", "").lower() == product_name.lower() and item.get("status") == "pending":
            item["status"] = "used"
            item["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
            
    _save_catalog(catalog)
    logger.info(f"[product_finder] Product '{product_name}' marked as 'used'.")

def _save_catalog(catalog: list[dict]) -> None:
    """Saves updated catalog list to product_catalog.json."""
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

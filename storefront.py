"""
storefront.py — Automated Storefront Manager & Dynamic Web Catalog.

Automatically tracks all campaign products into a JSON database (output/store_products.json)
and generates a mobile-optimized, high-converting HTML storefront (output/store.html)
served live via ngrok. Zero manual Linktree editing required!
"""

import json
import os
import logging
from datetime import datetime
import config

logger = logging.getLogger(__name__)

DB_PATH = "output/store_products.json"
HTML_PATH = "output/store.html"


def register_product(
    product_name: str,
    price: str,
    niche: str,
    affiliate_link: str,
    features: list[str] | None = None
) -> str:
    """
    Registers a product in the automated storefront catalog and rebuilds the live HTML page.

    Args:
        product_name: Product title
        price: Price string (e.g. "$24.99")
        niche: Product niche category
        affiliate_link: Tracked Amazon affiliate URL
        features: Optional list of product key features

    Returns:
        str: Live public URL of the automated storefront.
    """
    os.makedirs("output", exist_ok=True)

    products = []
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                products = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read existing store database: {e}")

    # Deduplicate: update product if it already exists, otherwise prepend
    existing_idx = next((i for i, p in enumerate(products) if p["name"].lower() == product_name.lower()), None)

    item = {
        "name": product_name,
        "price": price,
        "niche": niche,
        "link": affiliate_link,
        "features": features or [],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }

    if existing_idx is not None:
        products[existing_idx] = item
    else:
        products.insert(0, item)  # Latest products first

    # Save JSON database
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(products, f, indent=2, ensure_ascii=False)

    # Generate modern HTML Storefront
    _render_html(products)

    # Compute live URL via ngrok endpoint
    endpoint = os.getenv("PUBLIC_HOST_ENDPOINT", "https://splinter-bulgur-appease.ngrok-free.dev")
    store_url = f"{endpoint}/output/store.html"
    logger.info(f"[storefront] Product '{product_name}' registered. Live storefront URL: {store_url}")
    return store_url


def _render_html(products: list[dict]) -> None:
    """Generates a sleek, dark-mode, mobile-first affiliate storefront HTML page."""
    cards_html = ""
    for p in products:
        feats = "".join(f"<li>✨ {f}</li>" for f in p.get("features", [])[:3])
        cards_html += f"""
        <div class="card">
            <div class="badge">{p.get('niche', 'Trending')}</div>
            <h2 class="title">{p['name']}</h2>
            <div class="price">{p['price']}</div>
            <ul class="features">{feats}</ul>
            <a href="{p['link']}" target="_blank" rel="noopener noreferrer" class="buy-btn">
                🛒 View Deal on Amazon &rarr;
            </a>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Yasna Store — Featured Deals</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Outfit', sans-serif; }}
        body {{ background-color: #0b0f19; color: #f3f4f6; padding: 24px 16px; min-height: 100vh; display: flex; flex-direction: column; align-items: center; }}
        header {{ text-align: center; margin-bottom: 28px; width: 100%; max-width: 480px; }}
        .avatar {{ width: 88px; height: 88px; border-radius: 50%; border: 3px solid #6366f1; margin: 0 auto 12px; object-fit: cover; box-shadow: 0 0 20px rgba(99, 102, 241, 0.4); }}
        h1 {{ font-size: 24px; font-weight: 800; background: linear-gradient(135deg, #a5b4fc, #6366f1); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        p.sub {{ font-size: 14px; color: #9ca3af; margin-top: 4px; }}
        .grid {{ width: 100%; max-width: 480px; display: flex; flex-direction: column; gap: 18px; }}
        .card {{ background: #111827; border: 1px solid #1f2937; border-radius: 18px; padding: 20px; box-shadow: 0 10px 25px rgba(0,0,0,0.3); transition: transform 0.2s; position: relative; overflow: hidden; }}
        .card:hover {{ transform: translateY(-2px); border-color: #4f46e5; }}
        .badge {{ display: inline-block; padding: 4px 10px; background: rgba(99,102,241,0.15); color: #818cf8; font-size: 11px; font-weight: 700; text-transform: uppercase; border-radius: 20px; margin-bottom: 8px; letter-spacing: 0.5px; }}
        .title {{ font-size: 18px; font-weight: 700; color: #ffffff; margin-bottom: 4px; }}
        .price {{ font-size: 22px; font-weight: 800; color: #10b981; margin-bottom: 12px; }}
        .features {{ list-style: none; margin-bottom: 16px; font-size: 13px; color: #9ca3af; display: flex; flex-direction: column; gap: 4px; }}
        .buy-btn {{ display: block; width: 100%; text-align: center; background: linear-gradient(135deg, #10b981, #059669); color: #ffffff; text-decoration: none; font-weight: 700; font-size: 15px; padding: 14px; border-radius: 12px; box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3); transition: opacity 0.2s; }}
        .buy-btn:hover {{ opacity: 0.9; }}
        footer {{ margin-top: 36px; text-align: center; font-size: 12px; color: #4b5563; }}
    </style>
</head>
<body>
    <header>
        <h1>Yasna Store 🛍️</h1>
        <p class="sub">Curated Deals & Viral Tech Finds</p>
    </header>

    <div class="grid">
        {cards_html}
    </div>

    <footer>
        &copy; {datetime.now().year} Yasna Store • Amazon Associate Storefront
    </footer>
</body>
</html>
"""
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

import json
import os
import re
from storefront import _render_html

DB_PATH = "output/store_products.json"

def clean_storefront():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} not found.")
        return

    with open(DB_PATH, "r", encoding="utf-8") as f:
        products = json.load(f)

    # A product has an ASIN if it has a 10-digit ASIN (e.g. B0XXXXXXXX) in its link or if the link is a /dp/ or /gp/ page
    cleaned_products = []
    removed_products = []

    for p in products:
        link = p.get("link", "")
        # Check for 10-character Amazon ASIN pattern
        has_asin = False
        asin_match = re.search(r'/(dp|gp/product)/([A-Z0-9]{10})', link)
        if asin_match:
            has_asin = True
        elif "/dp/" in link or "/gp/" in link:
            has_asin = True
            
        if has_asin:
            cleaned_products.append(p)
        else:
            removed_products.append(p["name"])

    print(f"Total products before cleaning: {len(products)}")
    print(f"Removed {len(removed_products)} products lacking an ASIN: {removed_products}")
    print(f"Remaining products: {len(cleaned_products)}")

    # Save cleaned JSON
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned_products, f, indent=2, ensure_ascii=False)

    # Re-render the HTML pages
    _render_html(cleaned_products)
    print("Storefront HTML pages updated successfully!")

if __name__ == "__main__":
    clean_storefront()

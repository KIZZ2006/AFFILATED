import json
import os
import re
import time
import urllib.parse
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CATALOG_PATH = "product_catalog.json"

def main():
    if not os.path.exists(CATALOG_PATH):
        print(f"Error: {CATALOG_PATH} not found.")
        return

    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Filter pending products without ASIN
    to_resolve = [p for p in catalog if p.get("status") == "pending" and not p.get("asin")]
    print(f"Total products to resolve ASIN for: {len(to_resolve)}")
    if not to_resolve:
        print("No products need ASIN resolution.")
        return

    resolved_count = 0
    failed_count = 0

    with sync_playwright() as p:
        print("Launching Chromium browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        # Apply stealth if possible
        try:
            stealth = Stealth()
            stealth.apply_stealth_sync(page)
            print("Stealth applied to Playwright page.")
        except Exception as e:
            print(f"Warning: Could not apply stealth: {e}")

        for idx, item in enumerate(to_resolve):
            prod_name = item["product"]
            print(f"[{idx+1}/{len(to_resolve)}] Resolving: '{prod_name}'...")
            
            try:
                query = urllib.parse.quote_plus(prod_name.strip())
                url = f"https://www.amazon.in/s?k={query}"
                
                # Navigate to Amazon search
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # Wait up to 2 seconds for product tiles to appear
                try:
                    page.wait_for_selector('[data-asin]', timeout=3000)
                except:
                    pass
                
                content = page.content()
                matches = re.findall(r'data-asin="([A-Z0-9]{10})"', content)
                valid_asins = [m for m in matches if m.strip()]
                
                if valid_asins:
                    asin = valid_asins[0]
                    item["asin"] = asin
                    resolved_count += 1
                    print(f"  -> FOUND ASIN: {asin}")
                    
                    # Save catalog progressively
                    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                        json.dump(catalog, f, indent=2, ensure_ascii=False)
                else:
                    failed_count += 1
                    print("  -> Could not resolve ASIN (no matches found)")
                
            except Exception as e:
                failed_count += 1
                print(f"  -> Error resolving '{prod_name}': {e}")
            
            # Brief polite delay
            time.sleep(1.5)

        browser.close()

    print("\nResolution finished!")
    print(f"Successfully resolved: {resolved_count}")
    print(f"Failed to resolve: {failed_count}")

if __name__ == "__main__":
    main()

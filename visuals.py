import os
import logging
import requests
import random
import config

logger = logging.getLogger(__name__)

def fetch_product_images(image_urls: list[str], output_dir: str) -> list[str]:
    """
    Downloads real product images from Amazon product image URLs.
    Logs a warning if image_urls is empty or missing.
    """
    os.makedirs(output_dir, exist_ok=True)
    if not image_urls:
        logger.warning("[visuals] WARNING: Product lacks real product image URLs! Will fall back to stock Pexels visuals.")
        return []

    local_paths = []
    for idx, url in enumerate(image_urls):
        url = url.strip()
        if not url:
            continue
        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        elif ".webp" in url.lower():
            ext = ".webp"
        filename = f"product_img_{idx}{ext}"
        local_path = os.path.join(output_dir, filename)

        logger.info(f"[visuals] Downloading product image {idx + 1}/{len(image_urls)} from: {url}")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(resp.content)
            logger.info(f"[visuals] Product image saved to: {local_path}")
            local_paths.append(local_path)
        except Exception as e:
            logger.warning(f"[visuals] Failed to download product image from '{url}': {e}")

    if not local_paths:
        logger.warning("[visuals] WARNING: All product image downloads failed. Falling back to stock Pexels visuals.")
    return local_paths



def fetch_amazon_product_images_by_asin(asin: str, output_dir: str) -> list[str]:
    """
    Scrapes the Amazon.in product page for the main hi-res product image.
    Uses Playwright (Chromium headless browser) to load the page and render images.
    Returns list of local file paths (empty list if scrape fails).
    """
    import os
    import urllib.request
    from playwright.sync_api import sync_playwright
    from playwright_stealth.stealth import Stealth

    if not asin or len(asin) < 8:
        logger.warning(f"[visuals] Invalid ASIN '{asin}' — skipping Amazon image scrape.")
        return []
    os.makedirs(output_dir, exist_ok=True)
    url = f"https://www.amazon.in/dp/{asin}"
    
    logger.info(f"[visuals] Scraping Amazon.in product image using Playwright for ASIN: {asin}")
    try:
        with sync_playwright() as p:
            # Launch headless browser
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            
            # Apply stealth
            try:
                stealth = Stealth()
                stealth.apply_stealth_sync(page)
            except Exception as e:
                logger.warning(f"[visuals] Could not apply stealth to Playwright: {e}")
                
            # Navigate to Amazon
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Look for landingImage element
            img_element = page.locator("#landingImage")
            img_url = None
            if img_element.count() > 0:
                # Try to get high-res first
                data_old_hires = img_element.get_attribute("data-old-hires")
                if data_old_hires:
                    img_url = data_old_hires.strip()
                else:
                    img_url = img_element.get_attribute("src")
                    
            if not img_url:
                # Try og:image meta tag
                meta_element = page.locator("meta[property='og:image']")
                if meta_element.count() > 0:
                    img_url = meta_element.get_attribute("content")
                    
            browser.close()
            
            if not img_url:
                logger.warning(f"[visuals] Playwright could not find landingImage or og:image for ASIN {asin}")
                return []
                
            local_path = os.path.join(output_dir, f"product_amazon_{asin}.jpg")
            logger.info(f"[visuals] Downloading image from {img_url}...")
            
            # Direct media downloads from m.media-amazon.com do not require browser headers
            urllib.request.urlretrieve(img_url, local_path)
            
            if os.path.exists(local_path) and os.path.getsize(local_path) > 1000:
                size_kb = os.path.getsize(local_path) / 1024
                logger.info(f"[visuals] Playwright Amazon product image saved: {local_path} ({size_kb:.1f} KB)")
                return [local_path]
            else:
                logger.warning(f"[visuals] Downloaded image is missing or invalid size for ASIN {asin}")
                return []
                
    except Exception as e:
        logger.warning(f"[visuals] Playwright Amazon image scrape failed for ASIN {asin}: {e}")
        return []

NICHE_KEYWORD_POOLS = {
    # Existing niches (English market)
    "kitchen gadgets": ["kitchen", "cooking", "chef", "meal prep", "countertop", "home cooking", "recipe"],
    "home office": ["desk setup", "workspace", "computer desk", "office productivity", "home office", "desk decor"],
    "tech gadgets": ["gadgets", "tech", "electronics", "future tech", "smart device", "unboxing", "minimalist tech"],
    "fitness tech": ["workout", "fitness", "gym", "exercise", "training", "athlete", "recovery"],
    "car accessories": ["car interior", "driving", "automobile", "clean car", "car care", "road trip"],
    "home decor": ["home decor", "cozy room", "aesthetic room", "interior design", "lighting decor", "modern home"],
    "coffee gadgets": ["coffee maker", "espresso", "barista", "morning coffee", "coffee beans", "cafe aesthetic"],
    # Indian Amazon catalog niches (9% commission categories)
    "kitchen appliances and gadgets": ["indian kitchen", "cooking", "food prep", "kadai", "pressure cooker", "mixer grinder", "spices", "tawa"],
    "home decor and furniture": ["modern indian home", "living room", "cozy bedroom", "interior design", "aesthetic room", "home decor", "furniture"],
    "toys and baby products": ["children playing", "baby", "kids room", "educational toy", "colorful toys", "toddler", "child learning"],
    "apparel and fashion accessories": ["fashion", "styling", "outfit", "ethnic wear", "accessories", "traditional wear", "indian fashion"],
    "automotive and car accessories": ["car interior", "driving", "road trip", "car care", "dashboard", "parking", "automobile india"],
    "sports and outdoors": ["workout", "yoga", "cricket", "badminton", "gym", "fitness", "outdoor exercise", "morning run"],
    "lawn and garden": ["balcony garden", "terrace garden", "plants", "flowers", "outdoor", "gardening", "green home"],
}


def clean_search_query(query: str) -> str:
    """Strips brand names, model codes, and special characters to leave clean search terms."""
    import re
    # Remove alphanumeric codes (like N30, B0B8XNPQPN, 3-Tier)
    words = query.split()
    clean_words = []
    for w in words:
        if any(c.isdigit() for c in w):
            continue
        w_clean = re.sub(r'[^a-zA-Z]', '', w)
        if w_clean:
            clean_words.append(w_clean)
    
    # Take the last 2-3 words, which represent the core noun (e.g. "Vacuum Cleaner")
    if len(clean_words) >= 3:
        return " ".join(clean_words[-3:])
    return " ".join(clean_words)


def fetch_stock_clips(niche_keywords: list[str], count: int, output_dir: str, niche: str = "") -> tuple[list[str], list[str]]:
    """
    Queries the Pexels Video API for portrait-oriented stock video clips.
    
    Excludes clips used in the last 20 videos (via script_history.db).
    Rotates through a pool of related keyword terms for the given niche.
    
    Args:
        niche_keywords (list[str]): Keywords related to the product/niche.
        count (int): Number of unique video clips to download.
        output_dir (str): Folder where video clips should be saved.
        niche (str): Niche name for keyword pool lookup and tracking.
        
    Returns:
        tuple[list[str], list[str]]: (Paths to downloaded MP4s, List of Pexels clip IDs)
    """
    if not config.PEXELS_API_KEY:
        logger.warning("PEXELS_API_KEY is not configured. Returning empty stock clips list.")
        return [], []
        
    headers = {
        "Authorization": config.PEXELS_API_KEY
    }
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Get clip IDs used in the last 20 videos
    try:
        from script_history import get_recent_broll_clip_ids
        excluded_clip_ids = get_recent_broll_clip_ids(limit_videos=20)
    except Exception as e:
        logger.warning(f"[visuals] Could not fetch recent B-roll clip IDs: {e}")
        excluded_clip_ids = set()

    # Expand keywords using niche rotation pools
    niche_clean = niche.lower().strip()
    pool = NICHE_KEYWORD_POOLS.get(niche_clean, [])
    
    user_kws = []
    for kw in niche_keywords:
        kw = kw.strip()
        if not kw:
            continue
        cleaned_kw = clean_search_query(kw)
        if cleaned_kw:
            user_kws.append(cleaned_kw)

    search_keywords = user_kws + pool + ["aesthetic", "lifestyle", "minimalist"]
    
    # Deduplicate search_keywords order while preserving order
    unique_search_kws = []
    for kw in search_keywords:
        if kw.lower() not in [k.lower() for k in unique_search_kws]:
            unique_search_kws.append(kw)
            
    videos_metadata = []
    seen_video_ids = set()
    fallback_candidates = []
    
    # Collect metadata for videos across various keywords
    for keyword in unique_search_kws:
        if len(videos_metadata) >= count:
            break
            
        logger.info(f"[visuals] Searching Pexels for vertical videos using keyword: '{keyword}'")
        url = "https://api.pexels.com/videos/search"
        params = {
            "query": keyword,
            "orientation": "portrait",
            "per_page": count * 3,
            "page": random.randint(1, 10),  # Randomise page so different clips are returned each run
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                results = response.json().get("videos", [])
                logger.info(f"[visuals] Found {len(results)} videos matching '{keyword}'")
                for video in results:
                    video_id = str(video.get("id"))
                    if not video_id or video_id in seen_video_ids:
                        continue
                    seen_video_ids.add(video_id)
                    
                    if video_id in excluded_clip_ids:
                        fallback_candidates.append(video)
                    else:
                        videos_metadata.append(video)
            else:
                logger.warning(f"[visuals] Pexels search API returned error {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"[visuals] Failed to fetch videos from Pexels for query '{keyword}': {e}")
            
    # If not enough fresh clips were found, fall back to allowing recent clips
    if len(videos_metadata) < count and fallback_candidates:
        needed = count - len(videos_metadata)
        logger.warning(
            f"[visuals] WARNING: Too few new B-roll clips available for niche '{niche}'. "
            f"Falling back to reusing {needed} recent clip(s)."
        )
        videos_metadata.extend(fallback_candidates[:needed])

    # Shuffle so different clips are selected on every run (not always the same top N)
    random.shuffle(videos_metadata)
        
    if not videos_metadata:
        logger.warning("[visuals] No stock clips were found on Pexels using primary or fallback keywords.")
        return [], []
        
    downloaded_paths = []
    downloaded_clip_ids = []
    
    # Download the required number of video files
    for idx, video in enumerate(videos_metadata[:count]):
        video_id = str(video.get("id", f"unknown_{idx}"))
        video_files = video.get("video_files", [])
        
        if not video_files:
            logger.warning(f"[visuals] No video files available for Pexels video {video_id}, skipping.")
            continue
            
        # Select the best quality vertical file (prefer MP4)
        selected_file = None
        for file_info in video_files:
            if file_info.get("file_type") == "video/mp4":
                width = file_info.get("width", 0)
                height = file_info.get("height", 0)
                # Verify it is portrait (vertical)
                if height > width:
                    selected_file = file_info
                    break
                    
        if not selected_file:
            selected_file = video_files[0]
            
        download_url = selected_file.get("link")
        if not download_url:
            logger.warning(f"[visuals] Missing download URL for Pexels video {video_id}, skipping.")
            continue
            
        filename = f"pexels_{video_id}.mp4"
        local_filepath = os.path.join(output_dir, filename)
        
        logger.info(f"[visuals] Downloading Pexels clip {idx + 1}/{count} (ID: {video_id}) from: {download_url}")
        try:
            with requests.get(download_url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(local_filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            logger.info(f"[visuals] Downloaded successfully: {local_filepath}")
            downloaded_paths.append(local_filepath)
            downloaded_clip_ids.append(video_id)
        except Exception as e:
            logger.error(f"[visuals] Error downloading Pexels video {video_id}: {e}")
            
    return downloaded_paths, downloaded_clip_ids


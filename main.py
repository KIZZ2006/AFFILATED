import argparse
import json
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime

# Ensure project root is in Python's path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from script_generator import generate_script
from script_cleaner import clean_script
from voiceover import generate_voiceover
from captioner import generate_captions
from visuals import fetch_stock_clips, fetch_product_images
from assembler import assemble_video
from amazon_affiliate import generate_affiliate_link
from storefront import register_product
from product_finder import get_next_product

# Configure unified logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("yasna_pipeline")

CONTENT_LOG_FILE = "content_log.json"  # The ONE persistent record of all content
QUEUE_FILE = "queue.json"


def cleanup_previous_artifacts() -> None:
    """
    Runs at the START of every pipeline execution.
    Deletes all temporary binary assets (voiceovers, b-roll clips, product images,
    captions, raw/cleaned scripts, and assembled MP4s) for any queue entry that has
    already been sent to Telegram OR marked as posted.

    The video content metadata (product info, script, caption, links) is preserved
    permanently in content_log.json. Only the large binary files are deleted.
    """
    if not os.path.exists(QUEUE_FILE):
        return

    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        logger.warning(f"[cleanup] Could not read queue.json: {e}")
        return

    eligible_statuses = {"sent_to_telegram", "posted"}
    cleaned_count = 0

    for entry in queue:
        status = entry.get("review_status", "")
        if status not in eligible_statuses:
            continue

        vid = entry.get("video_id", "")
        video_path = entry.get("video_path", "")
        output_dir = os.path.dirname(video_path) if video_path else "output"

        # All temp files for this video_id
        temp_files = [
            video_path,
            os.path.join(output_dir, f"raw_script_{vid}.txt"),
            os.path.join(output_dir, f"cleaned_script_{vid}.txt"),
            os.path.join(output_dir, f"voiceover_{vid}.wav"),
            os.path.join(output_dir, f"captions_{vid}.ass"),
        ]
        for tf in temp_files:
            if tf and os.path.exists(tf):
                try:
                    os.remove(tf)
                    cleaned_count += 1
                except Exception as e:
                    logger.warning(f"[cleanup] Could not delete '{tf}': {e}")

        # Visuals directory (b-rolls + product images)
        visuals_dir = os.path.join(output_dir, f"visuals_{vid}")
        if os.path.exists(visuals_dir):
            try:
                shutil.rmtree(visuals_dir)
                cleaned_count += 1
            except Exception as e:
                logger.warning(f"[cleanup] Could not delete visuals dir '{visuals_dir}': {e}")

    if cleaned_count:
        logger.info(f"[cleanup] Removed {cleaned_count} temporary asset(s) from previous pipeline run(s).")
    else:
        logger.info("[cleanup] No stale temporary assets found. Output folder is clean.")


def log_content_record(record: dict) -> None:
    """
    Appends a complete content record to content_log.json.
    This is the ONE permanent store of content metadata — product info,
    script, caption text, affiliate link, hashtags. Everything else is ephemeral.
    """
    history = []
    if os.path.exists(CONTENT_LOG_FILE):
        try:
            with open(CONTENT_LOG_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception as e:
            logger.warning(f"[content_log] Could not read existing log: {e}")

    history.append(record)

    try:
        with open(CONTENT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        logger.info(f"[content_log] Record for video_id '{record.get('video_id')}' saved to {CONTENT_LOG_FILE}")
    except Exception as e:
        logger.error(f"[content_log] Failed to write content log: {e}")



def parse_args():
    parser = argparse.ArgumentParser(
        description="Yasna Content Pipeline - Autonomous Affiliate Video Creation"
    )
    parser.add_argument(
        "--product", 
        default="", 
        help="Optional name of the product. If omitted, auto-selects from catalog/AI"
    )
    parser.add_argument(
        "--price", 
        default="", 
        help="Optional price of the product"
    )
    parser.add_argument(
        "--features", 
        default="", 
        help="Optional comma-separated list of product features"
    )
    parser.add_argument(
        "--niche", 
        default="", 
        help="Optional niche category"
    )
    parser.add_argument(
        "--amazon_url",
        default="",
        help="Optional raw Amazon URL or 10-digit ASIN to link to"
    )
    return parser.parse_args()


def push_storefront_to_github():
    """
    Pushes the updated storefront files (JSON DB, HTML files, and product images)
    to GitHub repository to keep the live GitHub Pages storefront in sync.
    """
    import subprocess
    try:
        logger.info("[github] Pushing updated storefront to GitHub Pages...")
        # Add storefront files
        subprocess.run(["git", "add", "output/store_products.json", "output/store.html", "output/index.html", "output/assets/images/*"], capture_output=True)
        # Commit changes
        commit_res = subprocess.run(["git", "commit", "-m", "auto-update storefront: sync products"], capture_output=True, text=True)
        # Find current branch name
        branch_res = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
        branch = branch_res.stdout.strip() or "main"
        # Push to remote
        push_res = subprocess.run(["git", "push", "origin", branch], capture_output=True, text=True)
        if push_res.returncode == 0:
            logger.info("[github] Storefront successfully pushed to GitHub!")
        else:
            logger.warning(f"[github] Push failed: {push_res.stderr.strip()}")
    except Exception as e:
        logger.warning(f"[github] Failed to push storefront to GitHub: {e}")


def main():
    args = parse_args()

    # --- Clean up binary assets from previous delivered runs before generating new content ---
    cleanup_previous_artifacts()

    # Auto-select product if not specified explicitly via CLI
    product_item = {}
    if not args.product:
        logger.info("[main] Auto-selecting product from catalog/AI discovery...")
        product_item = get_next_product()
        product_name = product_item["product"]
        price = product_item["price"]
        niche = product_item["niche"]
        features_input = product_item["features"]
        amazon_url = product_item.get("asin", "")
    else:
        product_name = args.product
        price = args.price
        niche = args.niche
        features_input = args.features
        amazon_url = args.amazon_url

    # Parse list-based inputs
    if isinstance(features_input, str):
        features_list = [f.strip() for f in features_input.split(",") if f.strip()]
    else:
        features_list = features_input

    # Generate tracked Amazon Affiliate link (storefront registration deferred until product image is acquired)
    affiliate_url = generate_affiliate_link(product_name, amazon_url)
    
    logger.info("=" * 60)
    logger.info("YASNA CONTENT PIPELINE - VIDEO GENERATION INITIALIZED")
    logger.info(f"Product:  {product_name}")
    logger.info(f"Price:    {price}")
    logger.info(f"Niche:    {niche}")
    logger.info(f"Features: {features_list}")
    logger.info("=" * 60)
    
    missing_config = config.validate_config()
    if missing_config:
        logger.warning(f"Missing configuration: {missing_config}")

    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    video_id = str(uuid.uuid4())[:8]
    
    # Stage 1: Script Generation
    logger.info("[STAGE 1/6] Generating script...")
    try:
        raw_script = generate_script(
            product_name=product_name,
            price=price,
            features=features_list,
            niche=niche
        )
        raw_script_path = os.path.join(output_dir, f"raw_script_{video_id}.txt")
        with open(raw_script_path, "w", encoding="utf-8") as f:
            f.write(raw_script)
        logger.info(f"Script generated successfully: {raw_script_path}")
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        sys.exit(1)
        
    # Stage 2: Script Cleaning
    logger.info("[STAGE 2/6] Cleaning script...")
    try:
        cleaned_script = clean_script(raw_script)
        cleaned_script_path = os.path.join(output_dir, f"cleaned_script_{video_id}.txt")
        with open(cleaned_script_path, "w", encoding="utf-8") as f:
            f.write(cleaned_script)
        logger.info(f"Cleaned script saved: {cleaned_script_path}")
    except Exception as e:
        logger.error(f"Script cleaning failed: {e}")
        sys.exit(1)
        
    # Stage 3: Voiceover Generation
    logger.info("[STAGE 3/6] Synthesizing voiceover with edge-tts (Indian English)...")
    voiceover_path = os.path.join(output_dir, f"voiceover_{video_id}.wav")
    try:
        duration = generate_voiceover(cleaned_script, voiceover_path)
        logger.info(f"Voiceover track generated ({duration:.2f}s): {voiceover_path}")
    except Exception as e:
        logger.error(f"Voiceover synthesis failed: {e}")
        sys.exit(1)
        
    # Stage 4: Subtitles/Captions
    logger.info("[STAGE 4/6] Generating center word-burst captions with stable-ts...")
    ass_path = os.path.join(output_dir, f"captions_{video_id}.ass")
    try:
        word_timestamps = generate_captions(voiceover_path, ass_path, product_name=product_name)
        logger.info(f"ASS subtitle captions saved: {ass_path}")
    except Exception as e:
        logger.error(f"Captions generation failed: {e}")
        sys.exit(1)

    # Stage 5: Visuals Acquisition
    logger.info("[STAGE 5/6] Sourcing product images and secondary B-roll clips...")
    visuals_dir = os.path.join(output_dir, f"visuals_{video_id}")

    product_image_urls = product_item.get("images", [])
    product_image_paths = fetch_product_images(product_image_urls, visuals_dir)

    # If no catalog image URLs — scrape the real product image from Amazon.in using ASIN
    # The assembler interleaves this with B-roll (product image shown at narration keyword moments)
    if not product_image_paths:
        asin = product_item.get("asin", "") or amazon_url
        if asin:
            from visuals import fetch_amazon_product_images_by_asin
            logger.info(f"[STAGE 5/6] No catalog images found — scraping real product image for ASIN: {asin}")
            product_image_paths = fetch_amazon_product_images_by_asin(asin, visuals_dir)
            if product_image_paths:
                logger.info(f"[STAGE 5/6] Real product image acquired: {product_image_paths[0]}")
            else:
                logger.warning("[STAGE 5/6] Amazon image scrape failed — video will use B-roll only.")

    # B-roll: always fetch 2-3 clips to fill the video with context + lifestyle shots
    keywords = [product_name, niche] + features_list[:2]
    broll_paths, clip_ids = fetch_stock_clips(keywords, count=3, output_dir=visuals_dir, niche=niche)
    if clip_ids:
        try:
            from script_history import record_used_broll
            record_used_broll(clip_ids, video_id, niche)
        except Exception as e:
            logger.warning(f"Could not record used B-roll clip IDs: {e}")

    # Copy the product image to a permanent directory so the storefront can display it
    permanent_image_path = None
    if product_image_paths and len(product_image_paths) > 0:
        src_path = product_image_paths[0]
        # Resolve ASIN or clean filename suffix
        asin = product_item.get("asin", "") or amazon_url or "default"
        perm_img_dir = os.path.join("output", "assets", "images")
        os.makedirs(perm_img_dir, exist_ok=True)
        
        # Keep original extension
        ext = ".jpg"
        if src_path.lower().endswith(".png"):
            ext = ".png"
        elif src_path.lower().endswith(".webp"):
            ext = ".webp"
            
        dest_filename = f"product_{asin}{ext}"
        dest_path = os.path.join(perm_img_dir, dest_filename)
        try:
            shutil.copy2(src_path, dest_path)
            # Store path relative to output directory (used by web pages)
            permanent_image_path = os.path.join("output", "assets", "images", dest_filename)
            logger.info(f"[storefront] Product image copied to permanent path: {permanent_image_path}")
        except Exception as e:
            logger.warning(f"[storefront] Could not copy product image to permanent location: {e}")

    # Register the product on the storefront database and HTML page
    storefront_url = register_product(
        product_name=product_name,
        price=price,
        niche=niche,
        affiliate_link=affiliate_url,
        features=features_list,
        image_path=permanent_image_path
    )
    logger.info(f"Amazon Affiliate Link: {affiliate_url}")
    logger.info(f"Automated Storefront URL: {storefront_url}")
        
    # Stage 6: Video Assembly
    logger.info("[STAGE 6/6] Assembling final vertical video with FFmpeg...")
    final_video_path = os.path.join(output_dir, f"video_{video_id}.mp4")
    try:
        assembled_path = assemble_video(
            clip_paths=broll_paths,
            voiceover_path=voiceover_path,
            ass_path=ass_path,
            price_text=price,
            output_path=final_video_path,
            product_image_paths=product_image_paths,
            word_timestamps=word_timestamps,
            product_name=product_name,
            features=features_list
        )
        logger.info(f"Video assembled successfully: {assembled_path}")
    except Exception as e:
        logger.error(f"Video assembly failed: {e}")
        sys.exit(1)
        
    # Queueing video for decoupled publishing
    niche_tag = niche.replace(" ", "")
    prod_tag = product_name.replace(" ", "").replace("-", "")
    hashtags_list = [f"#{niche_tag}", f"#{prod_tag}", "#amazonfinds", "#affiliate", "#deals"]
    hashtags_str = " ".join(hashtags_list)

    caption_text = (
        f"🔥 {product_name} — only {price}!\n\n"
        f"✨ Features: {', '.join(features_list)}\n\n"
        f"👇 How to get the link:\n"
        f"1️⃣ Comment 'LINK' below and I'll send it directly to your DMs!\n"
        f"2️⃣ Click the link in bio to buy now: {storefront_url}\n\n"
        f"{hashtags_str}"
    )

    queue_entry = {
        "video_id": video_id,
        "video_path": os.path.abspath(final_video_path),
        "caption_text": caption_text,
        "hashtags": hashtags_list,
        "product_name": product_name,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "review_status": "pending"
    }

    queue_file = "queue.json"
    queue_data = []
    if os.path.exists(queue_file):
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                queue_data = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read existing queue.json: {e}")

    queue_data.append(queue_entry)

    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue_data, f, indent=2)

    # --- Persist complete content metadata to the permanent content log ---
    log_content_record({
        "video_id": video_id,
        "product_name": product_name,
        "price": price,
        "niche": niche,
        "features": features_list,
        "affiliate_url": affiliate_url,
        "storefront_url": storefront_url,
        "caption_text": caption_text,
        "hashtags": hashtags_list,
        "script": cleaned_script,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    from product_finder import mark_product_used
    mark_product_used(product_name)

    logger.info(f"[main] SUCCESS: Video generated and queued. video_id='{video_id}'")
    logger.info(f"[main] Affiliate link: {affiliate_url}")

    # --- Auto-send to Telegram for manual review (you upload to Instagram yourself) ---
    try:
        from telegram_notifier import send_video_to_telegram, send_copy_paste_to_telegram, cleanup_generated_assets
        logger.info("[main] Sending video to Telegram for your review...")
        sent = send_video_to_telegram(
            video_id=video_id,
            video_path=os.path.abspath(final_video_path),
            caption_text=caption_text,
            product_name=product_name
        )
        if sent:
            logger.info("[main] Video delivered to Telegram successfully!")

            # Send copy-paste caption/title/hashtags as a separate text message
            send_copy_paste_to_telegram(
                product_name=product_name,
                price=price,
                caption_text=caption_text,
                hashtags=hashtags_list,
                affiliate_url=affiliate_url,
                storefront_url=storefront_url,
            )

            # Update queue status
            queue_data[-1]["review_status"] = "sent_to_telegram"
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(queue_data, f, indent=2)

            # Delete ALL temp files — keep storage clean
            cleanup_generated_assets(
                video_id=video_id,
                output_dir=output_dir,
                video_path=os.path.abspath(final_video_path),
            )
            logger.info("[main] All temporary assets cleaned up. Storage is free.")
            
            # Auto-push updated storefront (HTML & assets) to GitHub Pages
            push_storefront_to_github()
        else:
            logger.warning("[main] Telegram delivery failed — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
            logger.warning("[main] Video kept on disk for manual retry: " + os.path.abspath(final_video_path))
    except Exception as e:
        logger.warning(f"[main] Could not send to Telegram: {e}")



if __name__ == "__main__":
    main()

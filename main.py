import argparse
import logging
import os
import sys

# Ensure project root is in Python's path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from script_generator import generate_script
from script_cleaner import clean_script
from voiceover import generate_voiceover
from captioner import generate_captions
from visuals import fetch_stock_clips
from assembler import assemble_video
from uploader_youtube import upload_to_youtube
from uploader_instagram import upload_to_instagram
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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Yasna Content Pipeline - Automated Affiliate Video Creation"
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
        "--publish", 
        default="", 
        help="Comma-separated platforms to publish to (e.g. 'youtube,instagram')"
    )
    parser.add_argument(
        "--amazon_url",
        default="",
        help="Optional raw Amazon URL or 10-digit ASIN to link to"
    )
    return parser.parse_args()

def main():
    args = parse_args()
    
    # Auto-select product if not specified explicitly via CLI
    if not args.product:
        logger.info("[main] No product CLI argument provided. Auto-selecting from Product Catalog / AI Discovery...")
        item = get_next_product()
        product_name = item["product"]
        price = item["price"]
        niche = item["niche"]
        features_input = item["features"]
    else:
        product_name = args.product
        price = args.price
        niche = args.niche
        features_input = args.features

    # Parse list-based inputs
    features_list = [f.strip() for f in features_input.split(",") if isinstance(features_input, str) and f.strip()] if isinstance(features_input, str) else features_input
    publish_platforms = [p.strip().lower() for p in args.publish.split(",") if p.strip()]

    # Generate tracked Amazon Affiliate link & register in automated storefront
    affiliate_url = generate_affiliate_link(product_name, args.amazon_url)
    storefront_url = register_product(
        product_name=product_name,
        price=price,
        niche=niche,
        affiliate_link=affiliate_url,
        features=features_list
    )
    logger.info(f"Amazon Affiliate Link: {affiliate_url}")
    logger.info(f"Automated Storefront URL: {storefront_url}")
    
    logger.info("=" * 60)
    logger.info("YASNA CONTENT PIPELINE - AUTOMATED CAMPAIGN INITIALIZED")
    logger.info(f"Product:  {product_name}")
    logger.info(f"Price:    {price}")
    logger.info(f"Niche:    {niche}")
    logger.info(f"Features: {features_list}")
    logger.info(f"Publishing Target: {publish_platforms if publish_platforms else 'None (Local-only)'}")
    logger.info("=" * 60)
    
    # Check for configurations
    missing_config = config.validate_config()
    if missing_config:
        logger.warning(
            f"Missing essential API keys in config: {missing_config}. "
            "Pipeline may fail if calling these modules."
        )

    # Setup directories
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    # Stage 1: Script Generation
    logger.info("[STAGE 1/7] Generating script using NVIDIA NIM API...")
    try:
        raw_script = generate_script(
            product_name=product_name,
            price=price,
            features=features_list,
            niche=niche
        )
        raw_script_path = os.path.join(output_dir, "raw_script.txt")
        with open(raw_script_path, "w", encoding="utf-8") as f:
            f.write(raw_script)
        logger.info(f"Raw script generated successfully. Saved to: {raw_script_path}")
        logger.info(f"\n--- Raw Script Text ---\n{raw_script}\n-----------------------\n")
    except Exception as e:
        logger.error(f"Script generation failed: {e}")
        sys.exit(1)
        
    # Stage 2: Script Cleaning
    logger.info("[STAGE 2/7] Segmenting and cleaning script with spaCy...")
    try:
        cleaned_script = clean_script(raw_script)
        cleaned_script_path = os.path.join(output_dir, "cleaned_script.txt")
        with open(cleaned_script_path, "w", encoding="utf-8") as f:
            f.write(cleaned_script)
        logger.info(f"Cleaned script generated successfully. Saved to: {cleaned_script_path}")
        logger.info(f"\n--- Cleaned Script Text ---\n{cleaned_script}\n---------------------------\n")
    except Exception as e:
        logger.error(f"Script cleaning failed: {e}")
        sys.exit(1)
        
    # Stage 3: Voiceover Generation
    logger.info("[STAGE 3/7] Synthesizing voiceover audio with Supertonic TTS...")
    voiceover_path = os.path.join(output_dir, "voiceover.wav")
    try:
        duration = generate_voiceover(cleaned_script, voiceover_path)
        logger.info(f"Voiceover track generated successfully. Duration: {duration:.2f} seconds")
    except Exception as e:
        logger.error(f"Voiceover synthesis failed: {e}")
        sys.exit(1)
        
    # Stage 4: Subtitles/Captions
    logger.info("[STAGE 4/7] Generating word-level timestamp captions with stable-ts...")
    ass_path = os.path.join(output_dir, "captions.ass")
    try:
        generate_captions(voiceover_path, ass_path)
        logger.info(f"ASS subtitle captions written to: {ass_path}")
    except Exception as e:
        logger.error(f"Captions generation failed: {e}")
        sys.exit(1)
        
    # Stage 5: Visuals Fetching
    logger.info("[STAGE 5/7] Querying Pexels stock video clips...")
    visuals_dir = os.path.join(output_dir, "visuals")
    # Build list of queries: starting from specific terms down to niche and features
    keywords = [product_name, niche] + features_list[:2]
    try:
        # Determine number of clips assuming each clip handles about 5-6s
        clip_count = max(3, int(duration // 5) + 1)
        logger.info(f"Fetching {clip_count} portrait clips to fill {duration:.2f}s timeline...")
        clip_paths = fetch_stock_clips(keywords, count=clip_count, output_dir=visuals_dir)
        logger.info(f"Clips downloaded successfully: {clip_paths}")
    except Exception as e:
        logger.error(f"Visuals downloading failed: {e}")
        sys.exit(1)
        
    # Stage 6: Video Assembly
    logger.info("[STAGE 6/7] Assembling final video with FFmpeg...")
    final_video_path = os.path.join(output_dir, "final_video.mp4")
    try:
        assembled_path = assemble_video(
            clip_paths=clip_paths,
            voiceover_path=voiceover_path,
            ass_path=ass_path,
            price_text=price,
            output_path=final_video_path
        )
        logger.info(f"Video assembled successfully: {assembled_path}")
    except Exception as e:
        logger.error(f"Video assembly failed: {e}")
        sys.exit(1)
        
    # Stage 7: Publishing (only run if assembly succeeded and target is provided)
    logger.info("[STAGE 7/7] Publishing campaigns...")
    for platform in publish_platforms:
        if platform == "youtube":
            logger.info("Uploading to YouTube Shorts...")
            try:
                title = f"{product_name} Review - {price}! #shorts"
                description = f"Looking for the best {product_name}? Here's why you need it: {', '.join(features_list)}."
                tags = [niche, product_name] + features_list
                video_url = upload_to_youtube(
                    video_path=final_video_path,
                    title=title,
                    description=description,
                    tags=tags
                )
                logger.info(f"YouTube upload finished! URL: {video_url}")
            except Exception as e:
                logger.error(f"YouTube upload failed: {e}")
        elif platform == "instagram":
            logger.info("Uploading to Instagram Reels via Graph API...")
            try:
                ig_caption = (
                    f"🔥 {product_name} — only {price}!\n\n"
                    f"✨ Features: {', '.join(features_list)}\n\n"
                    f"👇 How to get the link:\n"
                    f"1️⃣ Comment 'LINK' below and I'll send it directly to your DMs!\n"
                    f"2️⃣ Click the link in bio to buy now: {storefront_url}\n\n"
                    f"#{niche.replace(' ', '')} #{product_name.replace(' ', '').replace('-', '')} #amazonfinds #affiliate #deals"
                )
                ig_permalink = upload_to_instagram(
                    video_path=final_video_path,
                    caption=ig_caption
                )
                logger.info(f"Instagram Reel published! URL: {ig_permalink}")
            except Exception as e:
                logger.error(f"Instagram upload failed: {e}")
        else:
            logger.warning(f"Unsupported publishing platform specified: '{platform}'")
            
    logger.info("Pipeline completed successfully!")

if __name__ == "__main__":
    main()

import os
import logging
import requests
import config

logger = logging.getLogger(__name__)

def fetch_stock_clips(niche_keywords: list[str], count: int, output_dir: str) -> list[str]:
    """
    Queries the Pexels Video API for portrait-oriented stock video clips.
    
    Tries each niche keyword provided. If a query yields zero results, it continues to
    subsequent keywords, and ultimately falls back to general search queries (e.g. "aesthetic",
    "lifestyle") to ensure the video pipeline always has clips to assemble.
    
    Downloads the clips locally and returns a list of local file paths.
    
    Args:
        niche_keywords (list[str]): Keywords related to the product/niche (e.g. ["grinder", "kitchen"]).
        count (int): Number of unique video clips to download.
        output_dir (str): Folder where video clips should be saved.
        
    Returns:
        list[str]: Paths to the downloaded local .mp4 files.
    """
    if not config.PEXELS_API_KEY:
        raise ValueError("PEXELS_API_KEY is not configured. Please set it in your environment or .env file.")
        
    headers = {
        "Authorization": config.PEXELS_API_KEY
    }
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Setup candidate keyword list, incorporating generic fallbacks
    user_kws = [kw.strip() for kw in niche_keywords if kw.strip()]
    fallback_kws = ["aesthetic", "abstract", "minimalist", "lifestyle", "motion graphics"]
    search_keywords = user_kws + fallback_kws
    
    videos_metadata = []
    seen_video_ids = set()
    
    # Collect metadata for videos across various keywords
    for keyword in search_keywords:
        if len(videos_metadata) >= count:
            break
            
        logger.info(f"Searching Pexels for vertical videos using keyword: '{keyword}'")
        url = "https://api.pexels.com/videos/search"
        params = {
            "query": keyword,
            "orientation": "portrait",
            "per_page": count * 2  # Ask for more than needed so we have choices
        }
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            if response.status_code == 200:
                results = response.json().get("videos", [])
                logger.info(f"Found {len(results)} videos matching '{keyword}'")
                for video in results:
                    video_id = video.get("id")
                    if video_id and video_id not in seen_video_ids:
                        seen_video_ids.add(video_id)
                        videos_metadata.append(video)
            else:
                logger.warning(f"Pexels search API returned error {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Failed to fetch videos from Pexels for query '{keyword}': {e}")
            
    if not videos_metadata:
        raise RuntimeError("No stock clips were found on Pexels using primary or fallback keywords.")
        
    downloaded_paths = []
    
    # Download the required number of video files
    for idx, video in enumerate(videos_metadata[:count]):
        video_id = video.get("id", f"unknown_{idx}")
        video_files = video.get("video_files", [])
        
        if not video_files:
            logger.warning(f"No video files available for Pexels video {video_id}, skipping.")
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
                    
        # Fallback to first file if no explicitly vertical matches
        if not selected_file:
            selected_file = video_files[0]
            
        download_url = selected_file.get("link")
        if not download_url:
            logger.warning(f"Missing download URL for Pexels video {video_id}, skipping.")
            continue
            
        filename = f"pexels_{video_id}.mp4"
        local_filepath = os.path.join(output_dir, filename)
        
        logger.info(f"Downloading Pexels clip {idx + 1}/{count} (ID: {video_id}) from: {download_url}")
        try:
            with requests.get(download_url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(local_filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            logger.info(f"Downloaded successfully: {local_filepath}")
            downloaded_paths.append(local_filepath)
        except Exception as e:
            logger.error(f"Error downloading Pexels video {video_id}: {e}")
            
    if not downloaded_paths:
        raise RuntimeError("Failed to download any video clips successfully.")
        
    return downloaded_paths

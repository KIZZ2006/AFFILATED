import os
import logging
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import googleapiclient.discovery
import googleapiclient.errors
from googleapiclient.http import MediaFileUpload
import config

logger = logging.getLogger(__name__)

def upload_to_youtube(video_path: str, title: str, description: str, tags: list[str]) -> str:
    """
    Uploads a video to YouTube Shorts using the YouTube Data API v3.
    
    Loads existing credentials from config.YT_TOKEN_PATH. If the credentials
    are expired but have a refresh token, it will refresh and update the token file.
    
    Args:
        video_path (str): Local path to the MP4 file to upload.
        title (str): Video title (max 100 characters).
        description (str): Video description.
        tags (list[str]): List of metadata tags.
        
    Returns:
        str: The published YouTube Shorts URL.
    """
    if not os.path.exists(config.YT_TOKEN_PATH):
        raise FileNotFoundError(
            f"YouTube OAuth token file not found at: {config.YT_TOKEN_PATH}. "
            "Please authenticate first or place a valid OAuth token JSON."
        )
        
    logger.info(f"Loading YouTube credentials from: {config.YT_TOKEN_PATH}")
    creds = Credentials.from_authorized_user_file(
        config.YT_TOKEN_PATH,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    
    # Check if credentials need refreshing
    if creds and creds.expired and creds.refresh_token:
        logger.info("YouTube OAuth access token expired. Refreshing token...")
        try:
            creds.refresh(Request())
            # Save the refreshed token back to disk
            with open(config.YT_TOKEN_PATH, 'w') as f:
                f.write(creds.to_json())
            logger.info("YouTube access token refreshed and saved.")
        except Exception as e:
            logger.error(f"Failed to refresh YouTube credentials: {e}")
            raise e
            
    # Initialize the YouTube API v3 service
    youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
    
    # Prepare YouTube Shorts format requirements:
    # YouTube Shorts are automatically detected by vertical format/duration,
    # but placing #Shorts in metadata helps indexation.
    if "#Shorts" not in title and "#Shorts" not in description:
        description = f"{description}\n\n#Shorts"
        
    body = {
        'snippet': {
            'title': title[:100],  # Title character cap is 100
            'description': description[:5000],  # Description cap is 5000
            'tags': tags,
            'categoryId': '22'  # 'People & Blogs' (Standard YouTube Category)
        },
        'status': {
            'privacyStatus': 'public',  # Publish as public
            'selfDeclaredMadeForKids': False
        }
    }
    
    logger.info(f"Preparing resumable upload of '{video_path}'...")
    media = MediaFileUpload(
        video_path, 
        chunksize=1024 * 1024,  # 1MB chunks
        resumable=True, 
        mimetype='video/mp4'
    )
    
    request = youtube.videos().insert(
        part='snippet,status',
        body=body,
        media_body=media
    )
    
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress = int(status.progress() * 100)
                logger.info(f"YouTube Upload Progress: {progress}%")
        except googleapiclient.errors.HttpError as e:
            logger.error(f"YouTube API HttpError: {e.content.decode('utf-8')}")
            raise e
        except Exception as e:
            logger.error(f"Unexpected upload failure: {e}")
            raise e
            
    video_id = response.get('id')
    if not video_id:
        raise RuntimeError("YouTube upload response did not return a valid video ID.")
        
    shorts_url = f"https://youtube.com/shorts/{video_id}"
    logger.info(f"YouTube Short successfully published! URL: {shorts_url}")
    return shorts_url

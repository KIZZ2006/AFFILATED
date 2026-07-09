"""
script_history.py — SQLite database for script deduplication.

Tracks historical script hooks and full script texts to prevent generating
duplicate or overly similar video hooks across campaigns.
"""

import sqlite3
import os
import uuid
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "script_history.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialises the SQLite tables for script history and used B-roll clips."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS script_history (
                script_id TEXT PRIMARY KEY,
                hook_line TEXT NOT NULL,
                full_script_text TEXT NOT NULL,
                niche TEXT,
                product_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS used_broll (
                clip_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                niche TEXT,
                used_at TEXT NOT NULL
            )
        """)
        conn.commit()


def record_used_broll(clip_ids: list[str], video_id: str, niche: str) -> None:
    """Records Pexels clip IDs used in a video assembly to prevent recent reuse."""
    init_db()
    used_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as conn:
        for cid in clip_ids:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO used_broll (clip_id, video_id, niche, used_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(cid), video_id, niche, used_at)
                )
            except Exception as e:
                logger.warning(f"[script_history] Failed to record used clip {cid}: {e}")
        conn.commit()
    logger.info(f"[script_history] Recorded {len(clip_ids)} B-roll clip(s) for video_id '{video_id}'")


def get_recent_broll_clip_ids(limit_videos: int = 20) -> set[str]:
    """Retrieves set of clip IDs used in the last `limit_videos` videos."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        # Find the timestamp cutoff for the Nth most recent video
        cursor.execute(
            """
            SELECT clip_id FROM used_broll
            WHERE video_id IN (
                SELECT DISTINCT video_id FROM used_broll ORDER BY used_at DESC LIMIT ?
            )
            """,
            (limit_videos,)
        )
        rows = cursor.fetchall()
        return set(str(row["clip_id"]) for row in rows)


def calculate_word_overlap(text1: str, text2: str) -> float:
    """
    Computes Jaccard word overlap ratio between two text strings.
    Returns float between 0.0 (no overlap) and 1.0 (identical word sets).
    """
    words1 = set(w.lower().strip(".,!?\"'-") for w in text1.split() if len(w) > 2)
    words2 = set(w.lower().strip(".,!?\"'-") for w in text2.split() if len(w) > 2)

    if not words1 or not words2:
        return 0.0

    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)


def is_hook_duplicate(new_hook: str, threshold: float = 0.45) -> tuple[bool, float, str]:
    """
    Checks if new_hook has a word overlap above threshold with any existing stored hook.

    Returns:
        (is_duplicate, max_overlap_score, matched_hook)
    """
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT hook_line FROM script_history")
        rows = cursor.fetchall()

    max_overlap = 0.0
    matched_hook = ""

    for row in rows:
        existing_hook = row["hook_line"]
        overlap = calculate_word_overlap(new_hook, existing_hook)
        if overlap > max_overlap:
            max_overlap = overlap
            matched_hook = existing_hook

    if max_overlap >= threshold:
        logger.warning(
            f"[script_history] High hook overlap ({max_overlap:.2f}) detected with previous script: "
            f"'{matched_hook}'"
        )
        return True, max_overlap, matched_hook

    return False, max_overlap, ""


def save_script(hook_line: str, full_script_text: str, niche: str, product_name: str) -> str:
    """Saves a newly generated script to the local SQLite database."""
    init_db()
    script_id = str(uuid.uuid4())[:8]
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO script_history (script_id, hook_line, full_script_text, niche, product_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (script_id, hook_line.strip(), full_script_text.strip(), niche, product_name, created_at)
        )
        conn.commit()

    logger.info(f"[script_history] Script saved to database with ID '{script_id}'")

    # Also save/append to used_scripts.json for human-readable script auditing
    import json
    json_path = "used_scripts.json"
    history_entry = {
        "script_id": script_id,
        "product_name": product_name,
        "niche": niche,
        "hook_line": hook_line.strip(),
        "full_script_text": full_script_text.strip(),
        "created_at": created_at
    }
    
    history_data = []
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        except Exception as e:
            logger.warning(f"[script_history] Failed to read used_scripts.json: {e}")
            
    history_data.append(history_entry)
    
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2)
        logger.info(f"[script_history] Script entry appended to {json_path}")
    except Exception as e:
        logger.error(f"[script_history] Failed to write to {json_path}: {e}")

    return script_id

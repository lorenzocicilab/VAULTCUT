import sqlite3
import os
from src.logger import get_system_logger
from src.metadata.metadata_engine import process_clip

logger = get_system_logger()
DB_PATH = 'data/vaultcut.db'


def run_metadata_queue():
    conn = sqlite3.connect(DB_PATH)
    clips = conn.execute(
        "SELECT id FROM clips WHERE status='ready_to_upload' LIMIT 5"
    ).fetchall()
    conn.close()

    if not clips:
        logger.info("No clips ready for metadata generation")
        return

    logger.info(f"Found {len(clips)} clips to process")
    processed = 0
    failed = 0

    for (clip_id,) in clips:
        success = process_clip(clip_id)
        if success:
            processed += 1
        else:
            failed += 1
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE clips SET status='metadata_failed' WHERE id=?",
                (clip_id,)
            )
            conn.commit()
            conn.close()

    logger.info(f"Metadata queue done: {processed} success, {failed} failed")


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    run_metadata_queue()

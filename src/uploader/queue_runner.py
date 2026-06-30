import os
import sys
from src.uploader.upload_manager import process_upload_queue
from src.logger import get_system_logger

logger = get_system_logger()


def run_upload_queue():
    try:
        logger.info("Starting upload queue check")
        process_upload_queue()
    except Exception as e:
        logger.error(f"Upload queue error: {e}")


if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    run_upload_queue()

"""
VAULTCUT Logger
===============
Central logging system for the entire VAULTCUT project.
All modules use this to write logs to both the terminal and log files.

Logs are saved to the 'logs/' folder, one file per day.
Terminal output is colorful so you can easily see warnings/errors.

Usage in any module:
    from src.logger import get_logger
    logger = get_logger("downloader")
    logger.info("Starting download...")
    logger.warning("File is large, this may take a while")
    logger.error("Download failed!")
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Try to import colorlog for colorful terminal output
# If not installed yet, fall back to plain logging
try:
    import colorlog
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False


# ============================================================
# Configuration
# ============================================================

# This file lives at VAULTCUT/src/logger.py
# One dirname() → VAULTCUT/src/
# Two dirname() → VAULTCUT/        ← project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")

# Log file settings
MAX_LOG_SIZE_MB = 10         # Each log file max 10MB before rotating
MAX_LOG_FILES = 5            # Keep 5 backup files (50MB total)
LOG_LEVEL = logging.INFO     # Change to logging.DEBUG for more detail

# Colors for terminal output (requires colorlog)
LOG_COLORS = {
    "DEBUG":    "cyan",
    "INFO":     "green",
    "WARNING":  "yellow",
    "ERROR":    "red",
    "CRITICAL": "red,bold",
}


def setup_logs_directory():
    """Creates the logs folder if it doesn't exist."""
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR, exist_ok=True)


def get_logger(module_name: str) -> logging.Logger:
    """
    Creates and returns a logger for a specific module.
    Each module gets its own logger so you can see exactly where each log came from.

    Args:
        module_name: Name of your module, e.g. 'downloader', 'clipper', 'telegram'

    Returns:
        A configured Logger object

    Example:
        logger = get_logger("downloader")
        logger.info("Download started for: some_video.mp4")
        # Output: [2024-01-01 12:00:00] [INFO] [downloader] Download started for: some_video.mp4
    """
    setup_logs_directory()

    # Create logger with the module name
    logger = logging.getLogger(f"vaultcut.{module_name}")

    # Don't add handlers if they already exist (prevents duplicate logs)
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    # -------------------------------------------------------
    # Handler 1: Terminal (console) output - colorful
    # -------------------------------------------------------
    if HAS_COLORLOG:
        # Colorful format: module name and message in color
        console_formatter = colorlog.ColoredFormatter(
            fmt="%(log_color)s[%(asctime)s] [%(levelname)s] [%(name)s]%(reset)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors=LOG_COLORS
        )
    else:
        # Plain format if colorlog not installed
        console_formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(LOG_LEVEL)
    logger.addHandler(console_handler)

    # -------------------------------------------------------
    # Handler 2: Daily log file - plain text
    # File: logs/vaultcut_2024-01-01.log
    # -------------------------------------------------------
    today = datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(LOGS_DIR, f"vaultcut_{today}.log")

    file_formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # RotatingFileHandler: creates new file when max size is reached
    file_handler = RotatingFileHandler(
        filename=log_filename,
        maxBytes=MAX_LOG_SIZE_MB * 1024 * 1024,  # Convert MB to bytes
        backupCount=MAX_LOG_FILES,
        encoding="utf-8"
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(LOG_LEVEL)
    logger.addHandler(file_handler)

    # -------------------------------------------------------
    # Handler 3: Error-only log file
    # File: logs/errors.log - only WARNING and above
    # -------------------------------------------------------
    error_log_path = os.path.join(LOGS_DIR, "errors.log")
    error_handler = RotatingFileHandler(
        filename=error_log_path,
        maxBytes=MAX_LOG_SIZE_MB * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    error_handler.setFormatter(file_formatter)
    error_handler.setLevel(logging.WARNING)  # Only WARNING, ERROR, CRITICAL
    logger.addHandler(error_handler)

    # Don't pass messages up to root logger (prevents duplicates)
    logger.propagate = False

    return logger


def get_system_logger() -> logging.Logger:
    """
    Returns the main system logger (used by main.py and orchestration).
    This is the same as get_logger("system").
    """
    return get_logger("system")


# ============================================================
# Test the logger when run directly
# Command: python src/logger.py
# ============================================================
if __name__ == "__main__":
    print("Testing VAULTCUT Logger...")
    print(f"Logs directory: {LOGS_DIR}")
    print()

    # Test each module name
    test_modules = ["system", "downloader", "clipper", "telegram", "uploader"]

    for module in test_modules:
        logger = get_logger(module)
        logger.info(f"Test INFO message from {module}")
        logger.warning(f"Test WARNING message from {module}")

    # Test error logging
    logger = get_logger("test")
    logger.error("This is a test ERROR - everything is working correctly")

    print()
    print(f"✓ Log files saved in: {LOGS_DIR}")
    print(f"✓ Check 'logs/errors.log' for warnings and errors only")
    print(f"✓ Check today's log file for all messages")

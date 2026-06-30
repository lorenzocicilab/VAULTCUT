"""VAULTCUT Downloader Module - Phase 4"""

def run_download_queue():
    """Run the download queue"""
    from src.downloader.queue_runner import download_one_now
    return download_one_now()

def download_one_now():
    """Download one video immediately"""
    from src.downloader.queue_runner import download_one_now as _download
    return _download()

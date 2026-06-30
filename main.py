import os
import sys
import sqlite3
import signal
import threading
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR
from src.logger import get_system_logger
from src.database.init_db import init_db, get_stats
from src.uploader.queue_runner import run_upload_queue
from src.system_monitor.heartbeat import write_heartbeat
from src.system_monitor.status_tracker import notify_startup, notify_shutdown
from src.system_monitor.error_notifier import install_error_notifier
from src.system_monitor.daily_report import send_daily_report
from src.downloader.stuck_fixer import fix_stuck_downloads
from src.clipper.duplicate_detector import remove_duplicate_clips

logger = get_system_logger()
scheduler = None
shutting_down = False
bot_app = None


def handle_shutdown(signum, frame):
    global shutting_down, scheduler, bot_app
    if shutting_down:
        os._exit(0)
    shutting_down = True
    logger.info("VAULTCUT shutting down...")
    try:
        notify_shutdown()
    except Exception as e:
        logger.error(f"Shutdown notification error: {e}")
    try:
        if bot_app:
            bot_app.stop_running()
    except Exception:
        pass
    try:
        if scheduler and getattr(scheduler, "running", False):
            scheduler.shutdown(wait=False)
    except Exception as e:
        logger.error(f"Scheduler shutdown error: {e}")
    os._exit(0)


def start_bot_polling():
    import asyncio
    from src.telegram_bot.bot import get_application
    global bot_app

    async def _run():
        global bot_app
        bot_app = get_application()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_run())
    loop.run_forever()


def run_trend_engine():
    try:
        logger.info("Starting trend engine job")
        from src.trends.engine import run_trend_engine as _run
        _run()
    except Exception as e:
        logger.error(f"Trend engine job error: {e}")


def run_discovery_engine():
    try:
        logger.info("Starting discovery engine job")
        from src.discovery.discovery_engine import run_discovery
        run_discovery()
    except Exception as e:
        logger.error(f"Discovery engine job error: {e}")


def run_download_queue():
    try:
        logger.info("Starting download queue job")
        fix_stuck_downloads()
        from src.downloader.download_manager import run_download_batch
        run_download_batch()
    except Exception as e:
        logger.error(f"Download queue job error: {e}")


def run_transcription_queue():
    try:
        logger.info("Starting transcription queue job")
        from src.transcriber.queue_runner import run_transcription_queue
        run_transcription_queue()
    except Exception as e:
        logger.error(f"Transcription queue job error: {e}")


def run_analysis_queue():
    try:
        logger.info("Starting analysis queue job")
        from src.analyzer.queue_runner import run_analysis_queue
        run_analysis_queue()
    except Exception as e:
        logger.error(f"Analysis queue job error: {e}")


def run_clip_queue():
    try:
        logger.info("Starting clip queue job")
        from src.clipper.queue_runner import run_clip_queue as _run
        _run()
    except Exception as e:
        logger.error(f"Clip queue job error: {e}")


def run_metadata_job():
    try:
        logger.info("Starting metadata generation job")
        from src.metadata.queue_runner import run_metadata_queue
        run_metadata_queue()
    except Exception as e:
        logger.error(f"Metadata job error: {e}")


def run_telegram_job():
    try:
        logger.info("Starting Telegram notification job")
        from src.telegram_bot.queue_runner import run_telegram_queue
        run_telegram_queue()
    except Exception as e:
        logger.error(f"Telegram job error: {e}")


def run_upload_job():
    try:
        logger.info("Running upload queue job")
        run_upload_queue()
    except Exception as e:
        logger.error(f"Upload job error: {e}")


def run_heartbeat_job():
    try:
        write_heartbeat()
    except Exception as e:
        logger.error(f"Heartbeat job error: {e}")


def run_daily_report():
    try:
        logger.info("Sending daily report")
        send_daily_report()
    except Exception as e:
        logger.error(f"Daily report error: {e}")


def on_job_error(event):
    logger.error(f"Scheduler job failed [{event.job_id}]: {event.exception}")


def main():
    global scheduler

    logger.info("VAULTCUT starting up...")

    install_error_notifier()
    init_db()

    # Fix stuck downloads and duplicates at startup
    fix_stuck_downloads()
    dupes = remove_duplicate_clips()
    if dupes > 0:
        logger.info(f"Removed {dupes} duplicate clips at startup")

    conn = sqlite3.connect('data/vaultcut.db')
    conn.execute(
        "UPDATE clips SET status='ready_to_upload' WHERE status='metadata_failed'"
    )
    conn.commit()
    conn.close()

    stats = get_stats()
    logger.info(f"Database stats: {stats}")

    notify_startup()
    write_heartbeat()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
    bot_thread.start()
    logger.info("Telegram bot thread started")

    scheduler = BlockingScheduler()
    scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)

    scheduler.add_job(run_trend_engine, 'interval', hours=12,
                      id='trend_engine', max_instances=1, coalesce=True)
    scheduler.add_job(run_discovery_engine, 'interval', hours=1,
                      id='discovery_engine', max_instances=1, coalesce=True)
    scheduler.add_job(run_download_queue, 'interval', minutes=30,
                      id='download_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_transcription_queue, 'interval', minutes=30,
                      id='transcription_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_analysis_queue, 'interval', minutes=20,
                      id='analysis_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_clip_queue, 'interval', minutes=15,
                      id='clip_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_metadata_job, 'interval', minutes=30,
                      id='metadata_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_telegram_job, 'interval', minutes=30,
                      id='telegram_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_upload_job, 'interval', minutes=10,
                      id='upload_queue', max_instances=1, coalesce=True)
    scheduler.add_job(run_heartbeat_job, 'interval', minutes=1,
                      id='heartbeat', max_instances=1, coalesce=True)
    scheduler.add_job(run_daily_report, 'cron', hour=9, minute=0,
                      id='daily_report', max_instances=1, coalesce=True)

    logger.info("Running startup jobs...")
    run_download_queue()
    run_transcription_queue()
    run_analysis_queue()
    run_clip_queue()
    run_metadata_job()
    run_telegram_job()
    run_upload_job()

    print("=" * 50)
    print("VAULTCUT v1.0 - All Systems Active")
    print("=" * 50)
    print("  Trends:        every 12 hours")
    print("  Discovery:     every 1 hour")
    print("  Downloads:     every 30 minutes")
    print("  Transcription: every 30 minutes")
    print("  Analysis:      every 20 minutes")
    print("  Clip cutting:  every 15 minutes")
    print("  Metadata:      every 30 minutes")
    print("  Telegram:      every 30 minutes")
    print("  YouTube:       every 10 minutes")
    print("  Heartbeat:     every 1 minute")
    print("  Daily report:  every day at 09:00")
    print("  Bot commands:  /status")
    print("=" * 50)

    scheduler.start()


if __name__ == '__main__':
    main()

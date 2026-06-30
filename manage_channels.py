import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.chdir(os.path.abspath(os.path.dirname(__file__)))


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python manage_channels.py <command> [options]")
        print("Commands:")
        print("  add <channel_id> <name> <category>")
        print("  list")
        print("  remove <channel_id>")
        print("  download <video_id>")
        print("  queue")
        print("  transcribe")
        print("  analyze")
        print("  cut")
        print("  clips")
        print("  metadata now")
        print("  notify now")
        print("  status")
        print("  auth 'VAULTCUT Entertainment'")
        print("  upload now")
        print("  upload status")
        print("  upload test")
        return

    if args[0] == 'add' and len(args) >= 4:
        channel_id = args[1]
        name = args[2]
        category = args[3]
        from datetime import datetime
        conn = sqlite3.connect('data/vaultcut.db')
        conn.execute("""
            INSERT INTO monitored_channels
                (channel_id, channel_name, platform, category,
                 status, added_date, date_added)
            VALUES (?, ?, 'youtube', ?, 'active', ?, ?)
        """, (channel_id, name, category,
              datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        print(f"Added channel: {name} ({channel_id}) [{category}]")

    elif args[0] == 'list':
        conn = sqlite3.connect('data/vaultcut.db')
        rows = conn.execute(
            "SELECT id, channel_id, channel_name, category, status FROM monitored_channels"
        ).fetchall()
        conn.close()
        if not rows:
            print("No channels monitored.")
        else:
            print(f"\n{'ID':<5} {'Channel ID':<30} {'Name':<30} {'Category':<15} {'Status'}")
            print("-" * 90)
            for r in rows:
                print(f"{r[0]:<5} {r[1]:<30} {r[2]:<30} {r[3]:<15} {r[4]}")

    elif args[0] == 'remove' and len(args) > 1:
        channel_id = args[1]
        conn = sqlite3.connect('data/vaultcut.db')
        conn.execute(
            "DELETE FROM monitored_channels WHERE channel_id=?", (channel_id,)
        )
        conn.commit()
        conn.close()
        print(f"Removed channel: {channel_id}")

    elif args[0] == 'download' and len(args) > 1:
        video_id = args[1]
        from src.downloader.download_manager import DownloadManager
        manager = DownloadManager()
        print(f"Downloading {video_id}...")
        success = manager.download_video(video_id)
        print("Success" if success else "Failed")

    elif args[0] == 'queue':
        conn = sqlite3.connect('data/vaultcut.db')
        rows = conn.execute("""
            SELECT video_id, title, download_status, view_count
            FROM downloaded_videos
            WHERE download_status IN ('queued', 'downloading', 'failed')
            ORDER BY view_count DESC NULLS LAST
            LIMIT 20
        """).fetchall()
        conn.close()
        if not rows:
            print("Queue is empty.")
        else:
            print(f"\n{'Video ID':<20} {'Status':<15} {'Views':<12} Title")
            print("-" * 80)
            for r in rows:
                print(f"{r[0]:<20} {r[2]:<15} {str(r[3] or 0):<12} {(r[1] or '')[:40]}")

    elif args[0] == 'transcribe':
        print("Running transcription queue...")
        from src.transcriber.queue_runner import run_transcription_queue
        run_transcription_queue()
        print("Done.")

    elif args[0] == 'analyze':
        print("Running analysis queue...")
        from src.analyzer.queue_runner import run_analysis_queue
        run_analysis_queue()
        print("Done.")

    elif args[0] == 'cut':
        print("Running clip cutting queue...")
        from src.clipper.queue_runner import run_clip_queue
        run_clip_queue()
        print("Done.")

    elif args[0] == 'clips':
        conn = sqlite3.connect('data/vaultcut.db')
        rows = conn.execute("""
            SELECT id, video_id, title, status, approval_status,
                   virality_score, duration, target_channel
            FROM clips
            ORDER BY id DESC
            LIMIT 20
        """).fetchall()
        conn.close()
        if not rows:
            print("No clips found.")
        else:
            print(f"\n{'ID':<5} {'Video ID':<15} {'Score':<7} {'Status':<20} {'Approval':<12} {'Ch':<25} Title")
            print("-" * 110)
            for r in rows:
                print(
                    f"{r[0]:<5} {r[1]:<15} {str(r[5] or ''):<7} "
                    f"{str(r[3] or ''):<20} {str(r[4] or ''):<12} "
                    f"{str(r[7] or ''):<25} {(r[2] or '')[:30]}"
                )

    elif args[0] == 'metadata' and len(args) > 1 and args[1] == 'now':
        from src.metadata.queue_runner import run_metadata_queue
        print("Running metadata generation...")
        run_metadata_queue()
        conn = sqlite3.connect('data/vaultcut.db')
        clips = conn.execute("""
            SELECT id, generated_title, target_channel,
                   scheduled_upload_time
            FROM clips WHERE status='pending_approval'
        """).fetchall()
        conn.close()
        if clips:
            print(f"\nClips ready for approval ({len(clips)}):")
            for c in clips:
                print(f"  [{c[0]}] {c[1]}")
                print(f"        → {c[2]} @ {c[3]}")
        else:
            print("No clips moved to pending approval yet.")

    elif args[0] == 'notify' and len(args) > 1 and args[1] == 'now':
        from src.telegram_bot.queue_runner import run_telegram_queue
        print("Sending pending clips to Telegram...")
        run_telegram_queue()
        print("Done.")

    elif args[0] == 'status':
        conn = sqlite3.connect('data/vaultcut.db')

        def count(q, p=()):
            return conn.execute(q, p).fetchone()[0]

        print("\n" + "=" * 45)
        print("  VAULTCUT SYSTEM STATUS")
        print("=" * 45)
        print(f"  Trends in DB:        {count('SELECT COUNT(*) FROM trend_history')}")
        print(f"  Channels monitored:  {count('SELECT COUNT(*) FROM monitored_channels')}")
        print(f"  Videos queued:       {count("SELECT COUNT(*) FROM downloaded_videos WHERE download_status='queued'")}")
        print(f"  Videos downloaded:   {count("SELECT COUNT(*) FROM downloaded_videos WHERE download_status='completed'")}")
        print(f"  Videos transcribed:  {count("SELECT COUNT(*) FROM downloaded_videos WHERE transcription_status='complete'")}")
        print(f"  Videos analyzed:     {count("SELECT COUNT(*) FROM downloaded_videos WHERE analysis_status='complete'")}")
        print(f"  Clips pending cut:   {count("SELECT COUNT(*) FROM clips WHERE status='pending_clip'")}")
        print(f"  Clips ready:         {count("SELECT COUNT(*) FROM clips WHERE status='ready_to_upload'")}")
        print(f"  Clips in approval:   {count("SELECT COUNT(*) FROM clips WHERE status='pending_approval'")}")
        print(f"  Clips approved:      {count("SELECT COUNT(*) FROM clips WHERE status='approved'")}")
        print(f"  Clips rejected:      {count("SELECT COUNT(*) FROM clips WHERE status='rejected'")}")
        print(f"  Clips uploaded:      {count("SELECT COUNT(*) FROM clips WHERE status='uploaded'")}")
        print(f"  Upload queue:        {count("SELECT COUNT(*) FROM upload_schedule WHERE status='pending'")}")
        print("=" * 45 + "\n")
        conn.close()

    elif args[0] == 'auth' and len(args) > 1:
        from src.uploader.youtube_auth import get_credentials
        channel = ' '.join(args[1:])
        print(f"Starting OAuth for: {channel}")
        print("Browser will open for Google login...")
        try:
            creds = get_credentials(channel)
            if creds and creds.valid:
                print(f"✅ Authenticated: {channel}")
            else:
                print(f"❌ Auth failed for: {channel}")
        except Exception as e:
            print(f"❌ Error: {e}")

    elif args[0] == 'upload' and len(args) > 1 and args[1] == 'now':
        from src.uploader.queue_runner import run_upload_queue
        print("Running upload queue...")
        run_upload_queue()
        print("Done.")

    elif args[0] == 'upload' and len(args) > 1 and args[1] == 'status':
        from datetime import datetime
        conn = sqlite3.connect('data/vaultcut.db')
        print("\n" + "=" * 50)
        print("  UPLOAD QUEUE STATUS")
        print("=" * 50)
        pending = conn.execute(
            "SELECT COUNT(*) FROM upload_schedule WHERE status='pending'"
        ).fetchone()[0]
        uploaded = conn.execute(
            "SELECT COUNT(*) FROM upload_schedule WHERE status='uploaded'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM upload_schedule WHERE status='failed'"
        ).fetchone()[0]
        print(f"  Pending uploads:       {pending}")
        print(f"  Successfully uploaded: {uploaded}")
        print(f"  Failed:                {failed}")
        print("\n  Today's quota usage:")
        today = datetime.now().strftime('%Y-%m-%d')
        quotas = conn.execute(
            "SELECT channel, units_used, uploads_count FROM upload_quota WHERE date=?",
            (today,)
        ).fetchall()
        if quotas:
            for q in quotas:
                print(f"    {q[0]}: {q[1]} units / {q[2]} uploads")
        else:
            print("    No uploads today")
        print("\n  Next 5 scheduled:")
        upcoming = conn.execute("""
            SELECT us.clip_id, us.scheduled_time, us.channel, c.generated_title
            FROM upload_schedule us
            LEFT JOIN clips c ON c.id = us.clip_id
            WHERE us.status='pending'
            ORDER BY us.scheduled_time ASC LIMIT 5
        """).fetchall()
        if upcoming:
            for u in upcoming:
                title = (u[3] or 'Unknown')[:40]
                print(f"    [{u[0]}] {u[1]} → {u[2]}")
                print(f"          {title}")
        else:
            print("    No upcoming uploads scheduled")
        print("=" * 50 + "\n")
        conn.close()

    elif args[0] == 'upload' and len(args) > 1 and args[1] == 'test':
        from datetime import datetime
        from src.uploader.upload_manager import process_upload_queue
        print("⚠️  TEST UPLOAD MODE")
        print("This will force upload the next approved clip NOW")
        print("Bypassing the scheduled time check")
        confirm = input("Type YES to confirm: ")
        if confirm.strip() != 'YES':
            print("Cancelled.")
        else:
            conn = sqlite3.connect('data/vaultcut.db')
            conn.execute("""
                UPDATE upload_schedule
                SET scheduled_time='2020-01-01T00:00:00'
                WHERE status='pending'
                AND scheduled_time = (
                    SELECT MIN(scheduled_time)
                    FROM upload_schedule
                    WHERE status='pending'
                )
            """)
            conn.commit()
            conn.close()
            print("Scheduled time overridden. Running upload queue...")
            process_upload_queue()
            print("Done.")

    else:
        print(f"Unknown command: {' '.join(args)}")
        print("Run without arguments to see usage.")


if __name__ == '__main__':
    main()


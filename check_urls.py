import sqlite3

conn = sqlite3.connect('data/vaultcut.db')
cursor = conn.cursor()
cursor.execute('SELECT id, video_id, title, url FROM downloaded_videos WHERE download_status=? LIMIT 5', ('queued',))

for r in cursor.fetchall():
    print(f'{r[0]:3d} | {r[1]:15s} | {r[2][:40]:40s} | {r[3]}')

conn.close()

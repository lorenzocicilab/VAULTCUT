import sqlite3
conn = sqlite3.connect('data/vaultcut.db')

# Get existing columns
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(downloaded_videos)')
existing = [row[1] for row in cursor.fetchall()]

cols_to_add = [
    ('uploader', 'TEXT'),
    ('source_url', 'TEXT'),
    ('duration_seconds', 'INTEGER'),
    ('transcription_status', 'TEXT DEFAULT ''pending'''),
    ('analysis_status', 'TEXT DEFAULT ''pending'''),
]

for col_name, col_def in cols_to_add:
    if col_name not in existing:
        try:
            conn.execute(f'ALTER TABLE downloaded_videos ADD COLUMN {col_name} {col_def}')
            print(f'? Added: {col_name}')
        except Exception as e:
            print(f'? {col_name}: {e}')
    else:
        print(f'  Skip: {col_name}')

conn.commit()
conn.close()
print('\nDone. Run discovery engine again.')
